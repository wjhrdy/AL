import asyncio
import pygame
from io import BytesIO
import time
import warnings
import sys
import logging
from datetime import datetime, time as dt_time
import os
import aiohttp
import hashlib
import yaml
import threading
from soco import discover
import soco

warnings.filterwarnings('ignore', category=Warning)

class MusicIdentifier:
    def __init__(self, debug_mode=False, always_open=False):
        import os
        import yaml
        import logging
        import time
        import pygame

        self.debug_mode = debug_mode
        self.always_open = always_open
        self.start_time = time.time()

        # Sonos state tracking
        self.sonos_is_playing = False
        self.last_sonos_check = 0
        self.sonos_check_interval = 1.0
        self.sonos_check_interval_paused = 5.0

        # Set up logging
        log_level = logging.DEBUG if debug_mode else logging.INFO
        logging.basicConfig(
            level=log_level,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )
        self.logger = logging.getLogger(__name__)
        self.logger.debug("Initializing MusicIdentifier in debug mode" if debug_mode else "Initializing MusicIdentifier")

        # Display offset configuration
        self.display_config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'display-config.yaml')
        self.display_offset = {'x': 0, 'y': 0}
        self.load_display_config()

        # Load config
        self.config = self._load_config()
        self.config_lock = threading.Lock()
        self.last_config_update = time.time()

        # Create debug output directory
        if debug_mode:
            self.debug_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'debug_output')
            os.makedirs(self.debug_dir, exist_ok=True)

        # Initialize PyGame display
        pygame.init()
        self.screen_width = 800
        self.screen_height = 600
        self.screen = None
        self.font = None
        self.is_fullscreen = False
        self.is_stretched = False

        # Text scrolling
        self.scroll_start_time = 0
        self.SCROLL_PAUSE = 2.0
        self.SCROLL_SPEED = 100

        # Display modes
        self.NORMAL_RATIO = 16/9
        self.CRT_RATIO = 4/3
        self.stretch_factor = 1.0

        # Colors
        self.BACKGROUND_COLOR = (0, 0, 0)
        self.TEXT_COLOR = (255, 255, 255)

        # Song tracking
        self.last_identified = None
        self.last_song_time = None
        self.fade_duration = 1.0
        self.show_duration = 5.0
        self.current_background = None
        self.permanent_schedule = False

        # Schedule display timing
        self.last_schedule_display = 0
        self.schedule_showing = False
        self.schedule_show_start = 0

        # Screensaver state
        self.screensaver_pos = [100, 100]
        self.screensaver_velocity = [2, 2]
        self.screensaver_last_update = time.time()
        self.screensaver_color = (255, 255, 255)
        self.screensaver_color_direction = [1, 1, 1]

        # Initialize Sonos discovery
        self.sonos_speaker = None
        self.init_sonos()

    def _load_config(self):
        """Load the configuration from YAML file."""
        config_dir = os.path.dirname(os.path.abspath(__file__))
        config_path = os.path.join(config_dir, 'config.yaml')
        sample_config_path = os.path.join(config_dir, 'config.sample.yaml')

        if not os.path.exists(config_path) and os.path.exists(sample_config_path):
            self.logger.info("Creating config.yaml from sample...")
            try:
                import shutil
                shutil.copy2(sample_config_path, config_path)
                self.logger.info("Created config.yaml from sample")
            except Exception as e:
                self.logger.error(f"Error creating config from sample: {e}")
                return {'schedule': [], 'display': {'off_hours_message': 'Outside operating hours'}}

        try:
            with open(config_path, 'r') as f:
                return yaml.safe_load(f)
        except FileNotFoundError:
            self.logger.warning(f"Config file not found at {config_path}")
            return {'schedule': [], 'display': {'off_hours_message': 'Outside operating hours'}}
        except yaml.YAMLError as e:
            self.logger.error(f"Error parsing config file: {e}")
            return {'schedule': [], 'display': {'off_hours_message': 'Outside operating hours'}}

    def _save_config(self, config):
        """Save configuration to file."""
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.yaml')
        try:
            if os.path.exists(config_path):
                backup_path = config_path + '.bak'
                try:
                    import shutil
                    shutil.copy2(config_path, backup_path)
                except Exception as e:
                    self.logger.warning(f"Failed to create config backup: {e}")

            with open(config_path, 'w') as f:
                yaml.dump(config, f, default_flow_style=False)

            try:
                os.chmod(config_path, 0o666)
            except Exception as e:
                self.logger.warning(f"Failed to set config file permissions: {e}")

            self.logger.debug("Config file updated successfully")
            self.config = self._load_config()
            return True
        except Exception as e:
            self.logger.error(f"Error saving config: {e}")
            return False

    def _is_within_operating_hours(self):
        """Check if current time is within operating hours."""
        if self.always_open:
            return True

        if not self.config or 'schedule' not in self.config:
            return True

        current_time = datetime.now()
        current_day = current_time.strftime("%A")

        day_schedule = None
        for schedule_item in self.config['schedule']:
            if schedule_item['day'] == current_day:
                day_schedule = schedule_item
                break

        if not day_schedule:
            self.logger.debug(f"No schedule found for {current_day}, staying inactive")
            return False

        try:
            open_time = datetime.strptime(day_schedule['open'], "%I:%M %p").time()
            close_time = datetime.strptime(day_schedule['close'], "%I:%M %p").time()
            current_time = current_time.time()
            return open_time <= current_time <= close_time
        except ValueError as e:
            self.logger.error(f"Error parsing schedule times: {e}")
            return False

    def render_text_with_outline(self, text, font, color, outline_color=(0, 0, 0), outline_width=2):
        """Render text with an outline for better visibility."""
        outline_surfaces = []
        for dx in range(-outline_width, outline_width + 1):
            for dy in range(-outline_width, outline_width + 1):
                if dx*dx + dy*dy <= outline_width*outline_width:
                    outline_surface = font.render(text, True, outline_color)
                    outline_surfaces.append((outline_surface, (dx, dy)))

        text_surface = font.render(text, True, color)

        width = text_surface.get_width() + outline_width * 2
        height = text_surface.get_height() + outline_width * 2
        final_surface = pygame.Surface((width, height), pygame.SRCALPHA)

        for surface, (dx, dy) in outline_surfaces:
            final_surface.blit(surface, (dx + outline_width, dy + outline_width))

        final_surface.blit(text_surface, (outline_width, outline_width))

        return final_surface

    def draw_scrolling_text(self, text_surface, y_pos, alpha, max_width):
        """Draw scrolling text if it's wider than the screen."""
        text_width = text_surface.get_width()
        screen_width = pygame.display.get_surface().get_width()

        if text_width <= max_width:
            x_pos = (screen_width - text_width) // 2
            temp_surface = pygame.Surface((text_width, text_surface.get_height()), pygame.SRCALPHA)
            temp_surface.blit(text_surface, (0, 0))
            temp_surface.set_alpha(int(alpha * 255))
            self.screen.blit(temp_surface, (x_pos, y_pos))
        else:
            current_time = time.time()
            elapsed = current_time - self.scroll_start_time
            total_scroll_time = (text_width / self.SCROLL_SPEED) + (self.SCROLL_PAUSE * 2)

            if elapsed > total_scroll_time:
                self.scroll_start_time = current_time
                elapsed = 0

            visible_surface = pygame.Surface((max_width, text_surface.get_height()), pygame.SRCALPHA)

            if elapsed < self.SCROLL_PAUSE:
                x_scroll = 0
            elif elapsed > total_scroll_time - self.SCROLL_PAUSE:
                x_scroll = text_width - max_width
            else:
                scroll_elapsed = elapsed - self.SCROLL_PAUSE
                x_scroll = int(scroll_elapsed * self.SCROLL_SPEED)
                x_scroll = min(x_scroll, text_width - max_width)

            visible_surface.blit(text_surface, (-x_scroll, 0))
            visible_surface.set_alpha(int(alpha * 255))
            self.screen.blit(visible_surface, ((screen_width - max_width) // 2, y_pos))

    def _should_show_schedule(self, current_time):
        """Determine if the schedule should be shown based on timing and state."""
        if self.schedule_showing:
            schedule_duration = self.config.get('display', {}).get('schedule_duration', 10)
            return current_time - self.schedule_show_start < schedule_duration

        schedule_interval = self.config.get('display', {}).get('schedule_interval', 60)

        if not self.last_identified:
            schedule_interval = min(schedule_interval, 10)

        return current_time - self.last_schedule_display >= schedule_interval

    def wrap_text(self, text, font, max_width):
        """Wrap text to fit within a given width."""
        words = text.split(' ')
        lines = []
        current_line = []
        current_width = 0

        for word in words:
            word_surface = font.render(word, True, (0, 0, 0))
            word_width = word_surface.get_width()
            space_width = font.render(' ', True, (0, 0, 0)).get_width() if current_line else 0

            if current_width + word_width + space_width <= max_width:
                current_line.append(word)
                current_width += word_width + space_width
            else:
                if current_line:
                    lines.append(' '.join(current_line))
                current_line = [word]
                current_width = word_width

        if current_line:
            lines.append(' '.join(current_line))

        return lines

    def draw_window(self):
        """Draw the window contents."""
        if not pygame.display.get_init():
            return

        screen_width = pygame.display.get_surface().get_width()
        screen_height = pygame.display.get_surface().get_height()

        self.screen.fill((0, 0, 0))

        current_time = time.time()

        if self._should_show_schedule(current_time):
            if not self.schedule_showing:
                self.schedule_showing = True
                self.schedule_show_start = current_time
                self.last_schedule_display = current_time
        else:
            self.schedule_showing = False

        if self.current_background is not None and not self.schedule_showing:
            img_width = self.current_background.get_width()
            img_height = self.current_background.get_height()

            scale = min(screen_width / img_width,
                      screen_height / img_height)

            base_width = int(img_width * scale)
            base_height = int(img_height * scale)

            if self.is_stretched:
                target_width = int(base_width * (4/3))
                target_height = base_height
            else:
                target_width = base_width
                target_height = base_height

            scaled_surface = pygame.transform.smoothscale(
                self.current_background,
                (target_width, target_height)
            )

            x_pos, y_pos = self.apply_display_offset(
                (screen_width - target_width) // 2,
                (screen_height - target_height) // 2
            )
            self.screen.blit(scaled_surface, (x_pos, y_pos))

        if self.last_identified and self.last_song_time and not self.schedule_showing:
            current_time = time.time()
            elapsed_time = current_time - self.last_song_time

            if elapsed_time < self.show_duration:
                alpha = 255
            elif elapsed_time < (self.show_duration + self.fade_duration):
                fade_progress = (elapsed_time - self.show_duration) / self.fade_duration
                alpha = int(255 * (1 - fade_progress))
            else:
                alpha = 0

            if alpha > 0:
                title_font = pygame.font.Font(None, 72)
                artist_font = pygame.font.Font(None, 48)

                title_surface = self.render_text_with_outline(
                    self.last_identified['title'],
                    title_font,
                    (255, 255, 255),
                    (0, 0, 0),
                    3
                )
                artist_surface = self.render_text_with_outline(
                    self.last_identified['artist'],
                    artist_font,
                    (255, 255, 255),
                    (0, 0, 0),
                    2
                )

                max_width = int(screen_width * 0.8)

                _, title_y = self.apply_display_offset(0, screen_height // 2 - 40)
                _, artist_y = self.apply_display_offset(0, screen_height // 2 + 40)

                self.draw_scrolling_text(
                    title_surface,
                    title_y,
                    alpha,
                    max_width
                )

                x_pos, _ = self.apply_display_offset(screen_width // 2, 0)
                artist_rect = artist_surface.get_rect(center=(x_pos, artist_y))
                temp_surface = pygame.Surface(artist_surface.get_size(), pygame.SRCALPHA)
                temp_surface.blit(artist_surface, (0, 0))
                temp_surface.set_alpha(int(alpha))
                self.screen.blit(temp_surface, artist_rect)

        if self.schedule_showing or not self.last_identified:
            schedule_text = self._get_schedule_message()
            lines = schedule_text.split('\n')

            is_outside_hours = not self._is_within_operating_hours()

            size_multiplier = 0.75 if is_outside_hours else 1.0
            header_font_size = min(int(screen_height * 0.13 * size_multiplier), int(72 * size_multiplier))
            schedule_font_size = min(int(screen_height * 0.09 * size_multiplier), int(48 * size_multiplier))

            rendered_lines = []
            total_height = 0
            max_width = 0

            for i, line in enumerate(lines):
                if not line.strip():
                    continue

                font_size = header_font_size if i == 0 else schedule_font_size
                font = pygame.font.Font(None, font_size)

                text_surface = self.render_text_with_outline(
                    line.strip(),
                    font,
                    (255, 255, 255),
                    (0, 0, 0),
                    3 if not is_outside_hours else 2
                )

                rendered_lines.append(text_surface)
                total_height += text_surface.get_height()
                max_width = max(max_width, text_surface.get_width())

                if i < len(lines) - 1:
                    total_height += int(screen_height * (0.02 if not is_outside_hours else 0.015))

            vertical_shift = int(screen_height * 0.15) if is_outside_hours else 0
            _, base_y = self.apply_display_offset(0, (screen_height - total_height) // 2 - vertical_shift)
            current_y = base_y

            for text_surface in rendered_lines:
                x_pos, _ = self.apply_display_offset(screen_width//2, 0)
                text_rect = text_surface.get_rect(
                    centerx=x_pos,
                    top=current_y
                )
                self.screen.blit(text_surface, text_rect)
                current_y += text_surface.get_height() + int(screen_height * (0.02 if not is_outside_hours else 0.015))

            if is_outside_hours:
                message = self.config.get('display', {}).get('off_hours_message', 'Outside operating hours')
                font = pygame.font.Font(None, 48)

                max_width = int(screen_width * 0.7)
                wrapped_lines = self.wrap_text(message, font, max_width)

                line_height = font.get_linesize()
                total_height = len(wrapped_lines) * line_height

                rendered_lines = []
                for line in wrapped_lines:
                    text_surface = self.render_text_with_outline(
                        line,
                        font,
                        (255, 0, 0),
                        (0, 0, 0),
                        3
                    )
                    rendered_lines.append(text_surface)

                bottom_padding = int(screen_height * 0.05)
                start_y = screen_height - bottom_padding - total_height

                current_y = start_y
                for text_surface in rendered_lines:
                    x_pos, _ = self.apply_display_offset(screen_width//2, 0)
                    text_rect = text_surface.get_rect(
                        centerx=x_pos,
                        top=current_y
                    )
                    self.screen.blit(text_surface, text_rect)
                    current_y += line_height

        self.draw_notification()

        pygame.display.flip()

    async def display_album_art(self, track):
        """Display album art on screen with improved error handling and caching."""
        if not track or 'images' not in track:
            self.logger.warning("No album art found in track data")
            self.current_background = None
            return

        try:
            image_url = None
            spec_mode = self.config.get('display', {}).get('spec_mode', False)
            self.logger.info(f"Display mode - Spec mode: {spec_mode}")

            if spec_mode:
                if 'coverart' in track['images']:
                    image_url = track['images']['coverart']
                elif 'coverarthq' in track['images']:
                    image_url = track['images']['coverarthq']
            else:
                if 'coverarthq' in track['images']:
                    image_url = track['images']['coverarthq']
                elif 'coverart' in track['images']:
                    image_url = track['images']['coverart']

            if not image_url:
                self.logger.warning("No suitable album art URL found")
                self.current_background = None
                return

            self.logger.debug(f"Available image types: {list(track['images'].keys())}")
            self.logger.info(f"Selected image URL: {image_url}")

            cache_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cache')
            os.makedirs(cache_dir, exist_ok=True)

            cache_key = hashlib.md5(image_url.encode()).hexdigest()
            cache_path = os.path.join(cache_dir, f"{cache_key}.jpg")

            if os.path.exists(cache_path):
                self.logger.info("Loading album art from cache")
                try:
                    image_data = open(cache_path, 'rb').read()
                    self.logger.debug(f"Successfully read {len(image_data)} bytes from cache")
                except Exception as e:
                    self.logger.error(f"Failed to read from cache: {e}")
                    raise
            else:
                self.logger.info("Downloading album art...")
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(image_url, timeout=10) as response:
                            if response.status == 200:
                                image_data = await response.read()
                                self.logger.debug(f"Downloaded {len(image_data)} bytes")
                                with open(cache_path, 'wb') as f:
                                    f.write(image_data)
                            else:
                                raise Exception(f"Failed to download image: {response.status}")
                except Exception as e:
                    self.logger.error(f"Error during download: {e}")
                    raise

            self.logger.info("Loading image with Pygame...")
            try:
                image_stream = BytesIO(image_data)
                image = pygame.image.load(image_stream)

                if image.get_alpha():
                    image = image.convert_alpha()
                else:
                    image = image.convert()

                self.current_background = image
                self.logger.info("Successfully loaded and displayed album art")

            except pygame.error as e:
                self.logger.error(f"Pygame error loading image: {e}")
                raise

        except Exception as e:
            self.logger.error(f"Error displaying album art: {e}")
            if self.debug_mode:
                import traceback
                self.logger.debug(traceback.format_exc())
            self.current_background = None

    async def run(self):
        """Main application loop with Sonos integration."""
        try:
            pygame.display.set_caption("Music Identifier")
            if self.is_fullscreen:
                self.screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
            else:
                self.screen = pygame.display.set_mode((self.screen_width, self.screen_height))
            self.font = pygame.font.Font(None, 36)
            pygame.mouse.set_visible(False)

            # Start config update task if enabled
            if self.config.get('remote', {}).get('enabled', False):
                self.config_update_task = asyncio.create_task(self._update_config_loop())
                self.logger.debug("Config update loop started")
            else:
                self.config_update_task = None

            while True:
                self.handle_events()

                sonos_track = await self.get_sonos_track_info()

                if sonos_track:
                    if (not self.last_identified or
                        sonos_track['title'] != self.last_identified['title'] or
                        sonos_track['artist'] != self.last_identified['artist']):

                        self.last_identified = sonos_track
                        self.last_song_time = time.time()

                        await self.display_album_art(sonos_track)

                        self.logger.info(f"Sonos playing: {sonos_track['title']} by {sonos_track['artist']}")

                self.draw_window()
                await asyncio.sleep(0.1)

        except Exception as e:
            self.logger.error(f"Fatal error in run loop: {e}")
            if self.debug_mode:
                import traceback
                self.logger.debug(traceback.format_exc())
        finally:
            pygame.quit()

    def show_notification(self, title, message, duration=3):
        """Show a notification message on the screen."""
        self.notification = {
            'title': title,
            'message': message,
            'start_time': time.time(),
            'duration': duration
        }

    def draw_notification(self):
        """Draw the current notification if active."""
        if hasattr(self, 'notification'):
            current_time = time.time()
            if current_time - self.notification['start_time'] < self.notification['duration']:
                screen_width = pygame.display.get_surface().get_width()

                notification_surface = pygame.Surface((screen_width, 80))
                notification_surface.set_alpha(200)
                notification_surface.fill((0, 0, 0))
                self.screen.blit(notification_surface, (0, 0))

                title_font = pygame.font.Font(None, 36)
                message_font = pygame.font.Font(None, 24)

                title_text = title_font.render(self.notification['title'], True, (255, 255, 255))
                message_text = message_font.render(self.notification['message'], True, (200, 200, 200))

                x_pos, _ = self.apply_display_offset(screen_width//2, 0)
                title_rect = title_text.get_rect(centerx=x_pos, top=10)
                message_rect = message_text.get_rect(centerx=x_pos, top=45)

                self.screen.blit(title_text, title_rect)
                self.screen.blit(message_text, message_rect)
            else:
                delattr(self, 'notification')

    def _update_screensaver(self, text, font_size=36):
        """Update and render the screensaver text with wrapping and bouncing movement."""
        current_time = time.time()
        dt = current_time - self.screensaver_last_update
        self.screensaver_last_update = current_time

        self.screensaver_pos[0] += self.screensaver_velocity[0]
        self.screensaver_pos[1] += self.screensaver_velocity[1]

        for i in range(3):
            color_val = self.screensaver_color[i] + self.screensaver_color_direction[i]
            if color_val >= 255 or color_val <= 100:
                self.screensaver_color_direction[i] *= -1
                color_val = max(100, min(255, color_val))
            self.screensaver_color = tuple(
                self.screensaver_color[j] + self.screensaver_color_direction[j]
                if j == i else self.screensaver_color[j]
                for j in range(3)
            )

        font = pygame.font.Font(None, font_size)

        words = text.split()
        lines = []
        current_line = []
        max_width = self.screen_width * 0.8

        for word in words:
            test_line = ' '.join(current_line + [word])
            test_surface = font.render(test_line, True, self.screensaver_color)
            if test_surface.get_width() <= max_width:
                current_line.append(word)
            else:
                if current_line:
                    lines.append(' '.join(current_line))
                current_line = [word]
        if current_line:
            lines.append(' '.join(current_line))

        text_surfaces = [font.render(line, True, self.screensaver_color) for line in lines]
        total_height = sum(surface.get_height() for surface in text_surfaces)
        max_text_width = max(surface.get_width() for surface in text_surfaces)

        text_surface = pygame.Surface((max_text_width, total_height), pygame.SRCALPHA)
        current_y = 0
        for surface in text_surfaces:
            text_surface.blit(surface, ((max_text_width - surface.get_width()) // 2, current_y))
            current_y += surface.get_height()

        if self.screensaver_pos[0] <= 0 or self.screensaver_pos[0] + max_text_width >= self.screen_width:
            self.screensaver_velocity[0] *= -1
            self.screensaver_pos[0] = max(0, min(self.screensaver_pos[0], self.screen_width - max_text_width))

        if self.screensaver_pos[1] <= 0 or self.screensaver_pos[1] + total_height >= self.screen_height:
            self.screensaver_velocity[1] *= -1
            self.screensaver_pos[1] = max(0, min(self.screensaver_pos[1], self.screen_height - total_height))

        return text_surface

    async def _fetch_remote_config(self):
        """Fetch remote config from GitHub Gist."""
        remote_config = self.config.get('remote', {})
        if not remote_config.get('enabled') or not remote_config.get('url'):
            self.logger.debug("Remote config disabled or URL not set")
            return None

        try:
            base_url = remote_config['url'].rstrip('/')
            if 'gist.github.com' in base_url:
                gist_id = base_url.split('/')[-1]
                api_url = f"https://api.github.com/gists/{gist_id}"

                async with aiohttp.ClientSession() as session:
                    async with session.get(api_url) as response:
                        if response.status == 200:
                            gist_data = await response.json()
                            if gist_data.get('files'):
                                first_file = next(iter(gist_data['files'].values()))
                                url = first_file.get('raw_url')
                                if not url:
                                    self.logger.error("Could not find raw URL in Gist response")
                                    return None
                            else:
                                self.logger.error("No files found in Gist")
                                return None
                        else:
                            self.logger.error(f"Failed to fetch Gist metadata: HTTP {response.status}")
                            return None
            else:
                url = base_url + '/raw'

            self.logger.debug(f"Fetching remote config from: {url}")

            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        config_text = await response.text()
                        self.logger.debug(f"Successfully fetched remote config ({len(config_text)} bytes)")
                        try:
                            parsed_config = yaml.safe_load(config_text)
                            self.logger.debug(f"Parsed config: {parsed_config}")
                            return parsed_config
                        except yaml.YAMLError as e:
                            self.logger.error(f"Failed to parse remote config: {e}")
                            return None
                    else:
                        self.logger.error(f"Failed to fetch remote config: HTTP {response.status}")
                        return None
        except Exception as e:
            self.logger.error(f"Error fetching remote config: {e}")
            if self.debug_mode:
                import traceback
                self.logger.debug(traceback.format_exc())
            return None

    async def _update_config_loop(self):
        """Periodically update config from remote source."""
        self.logger.debug("Starting config update loop")
        await asyncio.sleep(2)

        while True:
            try:
                self.logger.debug("Checking for remote config updates...")
                remote_config = await self._fetch_remote_config()
                if remote_config:
                    self.logger.debug(f"Received remote config: {remote_config}")

                    with self.config_lock:
                        if yaml.dump(remote_config, sort_keys=True) != yaml.dump(self.config, sort_keys=True):
                            self.logger.debug("Remote config differs from local config, updating...")
                            if self._save_config(remote_config):
                                self.logger.info("Updated config file from remote source")
                                self.show_notification("Config Updated", "Successfully updated local configuration file")
                            else:
                                self.logger.warning("Failed to save updated config to file")
                                self.show_notification("Config Update Warning", "Failed to save remote config locally")
                        else:
                            self.logger.debug("Remote config matches local config, no update needed")
                else:
                    self.logger.debug("No remote config received")

                update_interval = self.config.get('remote', {}).get('update_interval', 3600)
                self.logger.debug(f"Next config check in {update_interval} seconds")
                await asyncio.sleep(update_interval)
            except Exception as e:
                self.logger.error(f"Error in config update loop: {e}")
                if self.debug_mode:
                    import traceback
                    self.logger.debug(f"Full traceback: {traceback.format_exc()}")
                await asyncio.sleep(60)

    def toggle_fullscreen(self):
        """Toggle between fullscreen and windowed mode."""
        self.is_fullscreen = not self.is_fullscreen
        if self.is_fullscreen:
            self.screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
        else:
            self.screen = pygame.display.set_mode((self.screen_width, self.screen_height))

    def toggle_stretch_mode(self):
        """Toggle between normal and stretched mode to compensate for 16:9 to 4:3 conversion."""
        self.is_stretched = not self.is_stretched

    def toggle_always_open(self):
        """Toggle between always open and scheduled hours mode."""
        self.always_open = not self.always_open
        self.show_notification(
            "Mode Changed",
            "Always Open: ON" if self.always_open else "Always Open: OFF"
        )
        self.logger.info(f"Always open mode: {self.always_open}")

    def handle_events(self):
        """Handle pygame events including fullscreen and stretch toggles."""
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                if hasattr(self, 'config_update_task') and self.config_update_task:
                    self.config_update_task.cancel()
                pygame.quit()
                sys.exit(0)
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_f:
                    self.toggle_fullscreen()
                elif event.key == pygame.K_s:
                    self.toggle_stretch_mode()
                elif event.key == pygame.K_o:
                    self.toggle_always_open()
                elif event.key == pygame.K_ESCAPE and self.is_fullscreen:
                    self.toggle_fullscreen()
                elif event.key == pygame.K_LEFT:
                    self.adjust_display_offset(dx=-5)
                elif event.key == pygame.K_RIGHT:
                    self.adjust_display_offset(dx=5)
                elif event.key == pygame.K_UP:
                    self.adjust_display_offset(dy=-5)
                elif event.key == pygame.K_DOWN:
                    self.adjust_display_offset(dy=5)
        return True

    def _get_schedule_message(self):
        """Get a formatted message about the schedule and current status."""
        if not self.config or 'schedule' not in self.config:
            return "AL is running 24/7"

        display_config = self.config.get('display', {})
        header = display_config.get('schedule_header', 'Operating Hours')
        time_format = display_config.get('schedule_time_format', '{open} - {close}')

        schedule_text = f"{header}:\n"

        hours_to_days = {}
        day_order = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
        day_indices = {day: i for i, day in enumerate(day_order)}

        for item in self.config['schedule']:
            hours = time_format.format(open=item['open'], close=item['close'])
            if hours not in hours_to_days:
                hours_to_days[hours] = []
            hours_to_days[hours].append(item['day'])

        for hours, days in hours_to_days.items():
            days.sort(key=lambda x: day_indices[x])

            ranges = []
            range_start = days[0]
            prev_idx = day_indices[days[0]]

            for day in days[1:]:
                curr_idx = day_indices[day]
                if curr_idx != prev_idx + 1:
                    if range_start == days[days.index(day)-1]:
                        ranges.append(range_start)
                    else:
                        ranges.append(f"{range_start}-{days[days.index(day)-1]}")
                    range_start = day
                prev_idx = curr_idx

            if range_start == days[-1]:
                ranges.append(range_start)
            else:
                ranges.append(f"{range_start}-{days[-1]}")

            schedule_text += f"{', '.join(ranges)}\n{hours}\n"

        return schedule_text

    def init_sonos(self):
        """Initialize connection to first available Sonos speaker."""
        try:
            speakers = discover()
            if speakers is None:
                self.logger.info("No Sonos speakers found on network")
                self.sonos_speaker = None
                return

            speakers_list = list(speakers)
            if speakers_list:
                self.sonos_speaker = speakers_list[0]
                self.logger.info(f"Connected to Sonos speaker: {self.sonos_speaker.player_name}")
            else:
                self.logger.info("No Sonos speakers found on network")
                self.sonos_speaker = None
        except Exception as e:
            self.logger.error(f"Error discovering Sonos speakers: {e}")
            self.sonos_speaker = None

    async def get_sonos_track_info(self):
        """Get current track info from Sonos speaker."""
        if not self.sonos_speaker:
            return None

        current_time = time.time()
        if current_time - self.last_sonos_check < (
            self.sonos_check_interval if self.sonos_is_playing
            else self.sonos_check_interval_paused
        ):
            if not self.sonos_is_playing:
                return None
            return self.last_identified if hasattr(self, 'last_identified') else None

        self.last_sonos_check = current_time

        try:
            transport_info = self.sonos_speaker.get_current_transport_info()
            current_state = transport_info.get('current_transport_state', '').lower()

            was_playing = self.sonos_is_playing
            self.sonos_is_playing = current_state == 'playing'

            if was_playing != self.sonos_is_playing:
                self.logger.info(f"Sonos playback state changed: {current_state}")
                if not self.sonos_is_playing:
                    self.last_identified = None
                    return None

            if self.sonos_is_playing:
                track_info = self.sonos_speaker.get_current_track_info()
                if track_info and track_info.get('title'):
                    return {
                        'title': track_info.get('title', 'Unknown Title'),
                        'artist': track_info.get('artist', 'Unknown Artist'),
                        'images': {
                            'coverart': track_info.get('album_art'),
                            'coverarthq': track_info.get('album_art')
                        }
                    }
            return None
        except Exception as e:
            self.logger.error(f"Error getting Sonos track info: {e}")
            self.sonos_is_playing = False
            return None

    def load_display_config(self):
        """Load display configuration from YAML file."""
        try:
            if os.path.exists(self.display_config_path):
                with open(self.display_config_path, 'r') as f:
                    config = yaml.safe_load(f)
                    if config and isinstance(config, dict):
                        self.display_offset = {
                            'x': config.get('offset_x', 0),
                            'y': config.get('offset_y', 0)
                        }
                        self.logger.info(f"Loaded display config: offset_x={self.display_offset['x']}, offset_y={self.display_offset['y']}")
        except Exception as e:
            self.logger.error(f"Error loading display config: {e}")

    def save_display_config(self):
        """Save display configuration to YAML file."""
        try:
            config = {
                'offset_x': self.display_offset['x'],
                'offset_y': self.display_offset['y']
            }
            with open(self.display_config_path, 'w') as f:
                yaml.dump(config, f, default_flow_style=False)
            self.logger.info(f"Saved display config: {config}")
        except Exception as e:
            self.logger.error(f"Error saving display config: {e}")

    def adjust_display_offset(self, dx=0, dy=0):
        """Adjust the display offset and save the configuration."""
        self.display_offset['x'] += dx
        self.display_offset['y'] += dy
        self.save_display_config()
        self.logger.debug(f"Adjusted display offset to: x={self.display_offset['x']}, y={self.display_offset['y']}")

    def apply_display_offset(self, x, y):
        """Apply the display offset to given coordinates."""
        return (x + self.display_offset['x'], y + self.display_offset['y'])

async def main():
    import argparse
    parser = argparse.ArgumentParser(description='Music Display App (Sonos)')
    parser.add_argument('--debug', action='store_true', help='Enable debug mode')
    parser.add_argument('--fullscreen', action='store_true', help='Start in fullscreen mode')

    try:
        args = parser.parse_args()
    except SystemExit as e:
        raise e
    except Exception as e:
        print(f"Warning: Error parsing arguments ({str(e)}), using defaults")
        class Args:
            debug = False
            fullscreen = False
        args = Args()

    app = MusicIdentifier(debug_mode=args.debug, always_open=False)
    if args.fullscreen:
        app.is_fullscreen = True
    await app.run()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nShutting down gracefully...")
    finally:
        pygame.quit()
        print("Cleanup complete")
