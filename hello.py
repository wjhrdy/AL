import asyncio
import pygame
from io import BytesIO
import time
import warnings
import sys
import logging
from datetime import datetime, time as dt_time
import os
import re
import copy
import uuid
import socket
import aiohttp
import hashlib
try:
    import segno  # QR code generation for the setup screen (optional)
except ImportError:
    segno = None
try:
    from PIL import Image as PILImage, ImageOps as PILImageOps
except ImportError:  # Pillow optional: without it, images load via pygame (no EXIF rotation)
    PILImage = None
    PILImageOps = None
try:
    import cv2  # OpenCV: decodes video announcements (optional)
except ImportError:
    cv2 = None
import yaml
import threading
from soco import discover
import soco

warnings.filterwarnings('ignore', category=Warning)

class _VideoPlayer:
    """Decodes a video file frame-by-frame for announcement playback.

    Frames are paced by wall-clock time (so playback runs at the correct speed
    regardless of the draw rate) and the clip loops to fill its display window.
    OpenCV's ORIENTATION_AUTO applies the rotation metadata that iPhones write,
    so portrait videos play upright. Decoded silently (no audio).
    """

    def __init__(self, path, logger):
        self.path = path
        self.logger = logger
        self.cap = cv2.VideoCapture(path)
        try:
            self.cap.set(cv2.CAP_PROP_ORIENTATION_AUTO, 1)
        except Exception:
            pass
        fps = self.cap.get(cv2.CAP_PROP_FPS) if self.cap else 0
        self.fps = fps if 0 < fps <= 120 else 30.0
        self.frame_count = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0) if self.cap else 0
        self.start_time = None
        self.decoded_idx = -1
        self.last_target = -1
        self.last_surface = None

    def _restart(self, now):
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        self.decoded_idx = -1
        self.start_time = now

    def get_surface(self, now):
        """Return the pygame surface for the frame due at wall-clock ``now``."""
        if self.cap is None or not self.cap.isOpened():
            return self.last_surface
        if self.start_time is None:
            self.start_time = now

        target = int((now - self.start_time) * self.fps)
        if self.frame_count > 0 and target >= self.frame_count:
            self._restart(now)
            target = 0
        if target == self.last_target and self.last_surface is not None:
            return self.last_surface

        while self.decoded_idx < target:
            if not self.cap.grab():
                self._restart(now)
                target = 0
                if not self.cap.grab():
                    return self.last_surface
            self.decoded_idx += 1

        ok, frame = self.cap.retrieve()
        if not ok or frame is None:
            return self.last_surface
        try:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            height, width = rgb.shape[:2]
            surface = pygame.image.frombuffer(rgb.tobytes(), (width, height), 'RGB').convert()
        except Exception as e:
            self.logger.error(f"Failed to convert video frame for {self.path}: {e}")
            return self.last_surface

        self.last_surface = surface
        self.last_target = target
        return surface

    def release(self):
        try:
            if self.cap is not None:
                self.cap.release()
        except Exception:
            pass
        self.cap = None


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
        self._font_path = self._find_font()
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

        # Text interrupt timing. The schedule is one interrupt; configured
        # announcements are additional interrupts on the same cadence.
        self.last_text_interrupt_display = 0
        self.text_interrupt_showing = False
        self.text_interrupt_show_start = 0
        self.active_text_interrupt = None
        self.text_interrupt_index = 0

        # Screensaver state
        self.screensaver_pos = [100, 100]
        self.screensaver_velocity = [2, 2]
        self.screensaver_last_update = time.time()
        self.screensaver_color = (255, 255, 255)
        self.screensaver_color_direction = [1, 1, 1]

        # Cache of loaded announcement images, keyed by path -> (mtime, surface)
        self._announcement_image_cache = {}
        # Active video announcement player (created lazily while a video shows)
        self._video_player = None
        # Throttle Sonos polling so a faster (video) draw loop doesn't hammer it
        self._last_sonos_poll = 0
        # QR setup-screen state
        self._qr_cache = {}
        self._setup_splash_until = 0

        # Initialize Sonos discovery
        self.sonos_speaker = None
        self.init_sonos()

    def _find_font(self):
        """Locate a custom font from the gitignored fonts/ folder.

        The font filename is set via ``display.font`` in the config. Custom font
        files live in ``fonts/`` (gitignored) so they are never committed to the
        repo. Returns None to fall back to the Pygame default font.
        """
        fonts_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fonts')
        os.makedirs(fonts_dir, exist_ok=True)

        font_name = self.config.get('display', {}).get('font')
        if not font_name:
            self.logger.info("No custom font configured (display.font); using Pygame default")
            return None

        font_path = os.path.join(fonts_dir, font_name)
        if os.path.exists(font_path):
            self.logger.info(f"Using custom font: {font_path}")
            return font_path

        self.logger.warning(
            f"Configured font '{font_name}' not found in {fonts_dir}; "
            "falling back to Pygame default"
        )
        return None

    def _make_font(self, size):
        return pygame.font.Font(self._font_path, size)

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

    def _config_number(self, value, default, config_key):
        """Parse a numeric config value with a safe fallback."""
        if value is None:
            return default

        try:
            parsed_value = float(value)
        except (TypeError, ValueError):
            self.logger.warning(f"Invalid {config_key} value: {value!r}; using {default}")
            return default

        if parsed_value < 0:
            self.logger.warning(f"Invalid {config_key} value: {value!r}; using {default}")
            return default

        return parsed_value

    def _get_text_interrupt_interval(self):
        """Get the shared interval for schedule and announcement interrupts."""
        display_config = self.config.get('display', {})
        interval = display_config.get('text_interrupt_interval')
        if interval is None:
            interval = display_config.get('schedule_interval', 60)

        interval = self._config_number(interval, 60, 'text_interrupt_interval')

        if not self.last_identified:
            interval = min(interval, 10)

        return interval

    def _get_text_interrupt_duration(self, interrupt=None):
        """Get the display duration for text interrupts."""
        if interrupt and interrupt.get('duration') is not None:
            return self._config_number(interrupt['duration'], 10, 'announcement.duration')

        display_config = self.config.get('display', {})
        duration = display_config.get('text_interrupt_duration')
        if duration is None:
            duration = display_config.get('schedule_duration', 10)

        return self._config_number(duration, 10, 'text_interrupt_duration')

    def _get_configured_announcements(self):
        """Return configured announcement interrupts."""
        display_config = self.config.get('display', {})
        announcements = display_config.get('announcements', [])
        if not isinstance(announcements, list):
            self.logger.warning("display.announcements must be a list")
            return []

        configured_announcements = []

        for i, announcement in enumerate(announcements):
            if isinstance(announcement, str):
                title = display_config.get('announcement_header', 'Announcement')
                lines = announcement.splitlines() or [announcement]
                duration = None
                enabled = True
            elif isinstance(announcement, dict):
                enabled = announcement.get('enabled', True)
                if not enabled:
                    continue

                # Image/video announcements: shown full-screen, fit to the display,
                # on the same rotation cadence as text announcements, with any
                # title/message rendered above the media.
                media_name = announcement.get('image') or announcement.get('video')
                if media_name:
                    is_video = bool(announcement.get('video'))
                    media_path = self._resolve_announcement_media(media_name)
                    if not media_path:
                        self.logger.warning(f"Announcement media not found: {media_name}")
                        continue
                    if is_video and cv2 is None:
                        self.logger.warning("Video announcement skipped: OpenCV not installed")
                        continue

                    media_title = announcement.get('title') or announcement.get('header') or ''
                    if isinstance(announcement.get('lines'), list):
                        media_lines = [str(line) for line in announcement['lines']]
                    else:
                        media_message = (
                            announcement.get('message') or
                            announcement.get('text') or
                            announcement.get('body') or
                            ''
                        )
                        media_lines = str(media_message).splitlines() if media_message else []
                    media_lines = [line for line in media_lines if str(line).strip()]

                    interrupt = {
                        'type': 'video' if is_video else 'image',
                        'title': str(media_title) if media_title else '',
                        'lines': media_lines,
                        'duration': announcement.get('duration'),
                    }
                    interrupt['video' if is_video else 'image'] = media_path
                    configured_announcements.append(interrupt)
                    continue

                title = (
                    announcement.get('title') or
                    announcement.get('header') or
                    display_config.get('announcement_header', 'Announcement')
                )
                duration = announcement.get('duration')

                if isinstance(announcement.get('lines'), list):
                    lines = [str(line) for line in announcement['lines']]
                else:
                    message = (
                        announcement.get('message') or
                        announcement.get('text') or
                        announcement.get('body') or
                        ''
                    )
                    lines = str(message).splitlines() if message else []
            else:
                self.logger.warning(f"Skipping invalid announcement at index {i}")
                continue

            if not enabled:
                continue

            lines = [line for line in lines if str(line).strip()]
            if not title and not lines:
                continue

            configured_announcements.append({
                'type': 'announcement',
                'title': str(title) if title else '',
                'lines': lines,
                'duration': duration
            })

        return configured_announcements

    def _get_text_interrupts(self):
        """Get the ordered list of text interrupts to rotate through."""
        interrupts = []

        if self.config and 'schedule' in self.config:
            interrupts.append({
                'type': 'schedule',
                'message': self._get_schedule_message()
            })

        interrupts.extend(self._get_configured_announcements())
        return interrupts

    def _should_show_text_interrupt(self, current_time):
        """Determine if a text interrupt should be shown based on timing."""
        if self.text_interrupt_showing:
            duration = self._get_text_interrupt_duration(self.active_text_interrupt)
            return current_time - self.text_interrupt_show_start < duration

        return current_time - self.last_text_interrupt_display >= self._get_text_interrupt_interval()

    def _start_next_text_interrupt(self, current_time):
        """Start the next text interrupt in the rotation."""
        interrupts = self._get_text_interrupts()
        if not interrupts:
            self.active_text_interrupt = None
            self.text_interrupt_showing = False
            self.last_text_interrupt_display = current_time
            return False

        self.active_text_interrupt = interrupts[self.text_interrupt_index % len(interrupts)]
        self.text_interrupt_index = (self.text_interrupt_index + 1) % len(interrupts)
        self.text_interrupt_showing = True
        self.text_interrupt_show_start = current_time
        self.last_text_interrupt_display = current_time

        # Release any video player when the new interrupt isn't that same video.
        if self._video_player is not None and self.active_text_interrupt.get('video') != self._video_player.path:
            self._release_video_player()
        return True

    def _release_video_player(self):
        """Release the active video announcement player, if any."""
        if self._video_player is not None:
            self._video_player.release()
            self._video_player = None

    def _stop_text_interrupt(self):
        """Stop the active text interrupt."""
        self.text_interrupt_showing = False
        self.active_text_interrupt = None
        self._release_video_player()

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

    def draw_schedule_interrupt(self, screen_width, screen_height, interrupt=None):
        """Draw the configured operating hours interrupt."""
        schedule_text = (interrupt or {}).get('message') or self._get_schedule_message()
        lines = [line.strip() for line in schedule_text.split('\n') if line.strip()]

        is_outside_hours = not self._is_within_operating_hours()

        size_multiplier = 0.75 if is_outside_hours else 1.0
        header_font_size = min(int(screen_height * 0.13 * size_multiplier), int(72 * size_multiplier))
        schedule_font_size = min(int(screen_height * 0.09 * size_multiplier), int(48 * size_multiplier))
        base_gap = int(screen_height * (0.02 if not is_outside_hours else 0.015))
        outline = 3 if not is_outside_hours else 2

        def render_block(scale):
            """Render the schedule lines at a font scale; return (surfaces, total, gap)."""
            h_size = max(12, int(header_font_size * scale))
            s_size = max(10, int(schedule_font_size * scale))
            gap = max(1, int(base_gap * scale))
            surfaces = []
            for i, line in enumerate(lines):
                font = self._make_font(h_size if i == 0 else s_size)
                surfaces.append(
                    self.render_text_with_outline(line, font, (255, 255, 255), (0, 0, 0), outline))
            total = sum(s.get_height() for s in surfaces)
            if len(surfaces) > 1:
                total += gap * (len(surfaces) - 1)
            return surfaces, total, gap

        # Compress the block to fit when there are many entries (e.g. extra days).
        # Reserve room at the bottom for the off-hours message when it is shown.
        available_height = int(screen_height * (0.60 if is_outside_hours else 0.92))

        rendered_lines, total_height, gap = render_block(1.0)
        if total_height > available_height and total_height > 0:
            rendered_lines, total_height, gap = render_block(available_height / total_height)

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
            current_y += text_surface.get_height() + gap

        if is_outside_hours:
            message = self.config.get('display', {}).get('off_hours_message', 'Outside operating hours')
            font = self._make_font(48)

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

    def _announcements_dir(self):
        """Folder holding uploaded announcement images (gitignored, device-local)."""
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'announcements')
        os.makedirs(path, exist_ok=True)
        return path

    def _resolve_announcement_media(self, name):
        """Resolve an announcement media filename to an existing path, or None."""
        if not name:
            return None
        path = os.path.join(self._announcements_dir(), os.path.basename(str(name)))
        return path if os.path.exists(path) else None

    def _load_announcement_image(self, path):
        """Load (and cache by mtime) an announcement image as a pygame surface."""
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            return None
        cached = self._announcement_image_cache.get(path)
        if cached and cached[0] == mtime:
            return cached[1]
        surface = self._load_image_surface(path)
        if surface is None:
            return None
        self._announcement_image_cache[path] = (mtime, surface)
        return surface

    def _load_image_surface(self, path):
        """Load an image as a pygame surface, honoring EXIF orientation when possible.

        Phone photos store rotation in EXIF metadata that pygame ignores (so a
        portrait photo shows sideways). Pillow's exif_transpose applies it; if
        Pillow is unavailable we fall back to pygame's loader.
        """
        if PILImage is not None:
            try:
                with PILImage.open(path) as img:
                    img = PILImageOps.exif_transpose(img)
                    img = img.convert('RGBA')
                    return pygame.image.fromstring(img.tobytes(), img.size, 'RGBA').convert_alpha()
            except Exception as e:
                self.logger.warning(f"Pillow could not load {path} ({e}); using pygame loader")
        try:
            return pygame.image.load(path).convert_alpha()
        except Exception as e:
            self.logger.error(f"Failed to load announcement image {path}: {e}")
            return None

    def draw_image_interrupt(self, screen_width, screen_height, interrupt):
        """Draw an image announcement (text on top, image fit below)."""
        surface = self._load_announcement_image(interrupt.get('image'))
        if surface is None:
            return
        self._draw_media_interrupt(screen_width, screen_height, interrupt, surface)

    def draw_video_interrupt(self, screen_width, screen_height, interrupt):
        """Draw a video announcement (text on top, video frame fit below)."""
        if cv2 is None:
            return
        path = interrupt.get('video')
        if not path:
            return
        if self._video_player is None or self._video_player.path != path:
            if self._video_player is not None:
                self._video_player.release()
            self._video_player = _VideoPlayer(path, self.logger)
        surface = self._video_player.get_surface(time.time())
        if surface is None:
            return
        # Fast (non-smooth) scaling for video frames — cheaper per frame.
        self._draw_media_interrupt(screen_width, screen_height, interrupt, surface, smooth=False)

    def _draw_media_interrupt(self, screen_width, screen_height, interrupt, surface, smooth=True):
        """Render an interrupt's title/message on top, then the given media
        surface zoomed to fit the remaining space below (aspect ratio preserved).
        Shared by image and video announcements. ``smooth`` uses smoothscale
        (images); video uses fast scale to keep per-frame cost low."""
        title = interrupt.get('title', '')
        lines = interrupt.get('lines', [])
        top_padding = int(screen_height * 0.04)
        line_gap = int(screen_height * 0.015)
        text_surfaces = []

        if title or lines:
            max_text_width = int(screen_width * 0.9)
            title_font = self._make_font(min(int(screen_height * 0.10), 60))
            body_font = self._make_font(min(int(screen_height * 0.07), 40))
            if title:
                text_surfaces.append(
                    self.render_text_with_outline(str(title), title_font, (255, 255, 255), (0, 0, 0), 3))
            for line in lines:
                for wrapped in self.wrap_text(str(line), body_font, max_text_width):
                    text_surfaces.append(
                        self.render_text_with_outline(wrapped, body_font, (255, 255, 255), (0, 0, 0), 2))

        current_y = top_padding
        for text_surface in text_surfaces:
            x_pos, _ = self.apply_display_offset(screen_width // 2, 0)
            rect = text_surface.get_rect(centerx=x_pos, top=current_y)
            self.screen.blit(text_surface, rect)
            current_y += text_surface.get_height() + line_gap

        # Compute the area available for the media (below the text, if any).
        if text_surfaces:
            text_block_height = sum(s.get_height() for s in text_surfaces) + line_gap * (len(text_surfaces) - 1)
            gap_below_text = int(screen_height * 0.03)
            avail_top = top_padding + text_block_height + gap_below_text
            avail_height = max(1, screen_height - avail_top - top_padding)
        else:
            avail_top = 0
            avail_height = screen_height
        avail_width = screen_width

        media_width = surface.get_width()
        media_height = surface.get_height()
        if media_width <= 0 or media_height <= 0:
            return

        scale = min(avail_width / media_width, avail_height / media_height)
        target_width = int(media_width * scale)
        target_height = int(media_height * scale)
        if self.is_stretched:
            target_width = int(target_width * (4 / 3))

        scaler = pygame.transform.smoothscale if smooth else pygame.transform.scale
        scaled_surface = scaler(surface, (target_width, target_height))
        x_pos, y_pos = self.apply_display_offset(
            (screen_width - target_width) // 2,
            avail_top + (avail_height - target_height) // 2
        )
        self.screen.blit(scaled_surface, (x_pos, y_pos))

    def draw_announcement_interrupt(self, screen_width, screen_height, interrupt):
        """Draw a configured text announcement interrupt."""
        title = interrupt.get('title', '')
        lines = interrupt.get('lines', [])
        max_width = int(screen_width * 0.75)

        title_font = self._make_font(min(int(screen_height * 0.13), 72))
        body_font = self._make_font(min(int(screen_height * 0.09), 48))

        text_lines = []
        if title:
            text_lines.append((title, title_font, 3))

        for line in lines:
            wrapped_lines = self.wrap_text(str(line), body_font, max_width)
            for wrapped_line in wrapped_lines:
                text_lines.append((wrapped_line, body_font, 2))

        rendered_lines = []
        total_height = 0
        line_gap = int(screen_height * 0.025)

        for text, font, outline_width in text_lines:
            text_surface = self.render_text_with_outline(
                text,
                font,
                (255, 255, 255),
                (0, 0, 0),
                outline_width
            )
            rendered_lines.append(text_surface)
            total_height += text_surface.get_height()

        if rendered_lines:
            total_height += line_gap * (len(rendered_lines) - 1)

        _, base_y = self.apply_display_offset(0, (screen_height - total_height) // 2)
        current_y = base_y

        for text_surface in rendered_lines:
            x_pos, _ = self.apply_display_offset(screen_width//2, 0)
            text_rect = text_surface.get_rect(
                centerx=x_pos,
                top=current_y
            )
            self.screen.blit(text_surface, text_rect)
            current_y += text_surface.get_height() + line_gap

    def draw_text_interrupt(self, screen_width, screen_height, interrupt):
        """Draw the active text interrupt."""
        interrupt_type = interrupt.get('type')
        if interrupt_type == 'image':
            self.draw_image_interrupt(screen_width, screen_height, interrupt)
        elif interrupt_type == 'video':
            self.draw_video_interrupt(screen_width, screen_height, interrupt)
        elif interrupt_type == 'announcement':
            self.draw_announcement_interrupt(screen_width, screen_height, interrupt)
        else:
            self.draw_schedule_interrupt(screen_width, screen_height, interrupt)

    def _local_ip(self):
        """Best-effort LAN IP address of this device."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.connect(("8.8.8.8", 80))
                return s.getsockname()[0]
            finally:
                s.close()
        except Exception:
            try:
                return socket.gethostbyname(socket.gethostname())
            except Exception:
                return "127.0.0.1"

    def _config_url(self):
        """URL of the config web app, for the setup QR code."""
        try:
            port = int(os.environ.get('AL_WEB_PORT', '8080'))
        except (TypeError, ValueError):
            port = 8080
        return f"http://{self._local_ip()}:{port}"

    def _get_qr_surface(self, data, target_px):
        """Render (and cache) a QR code for ``data`` as a pygame surface ~target_px."""
        if segno is None:
            return None
        key = (data, target_px)
        if key in self._qr_cache:
            return self._qr_cache[key]
        try:
            rows = [list(row) for row in segno.make(data, error='m').matrix]
        except Exception as e:
            self.logger.error(f"QR generation failed: {e}")
            return None
        modules = len(rows)
        border = 4
        full = modules + 2 * border
        base = pygame.Surface((full, full))
        base.fill((255, 255, 255))
        for y, row in enumerate(rows):
            for x, bit in enumerate(row):
                if bit:
                    base.set_at((x + border, y + border), (0, 0, 0))
        scale = max(1, target_px // full)
        surface = pygame.transform.scale(base, (full * scale, full * scale))
        self._qr_cache[key] = surface
        return surface

    def draw_setup_screen(self, screen_width, screen_height):
        """Draw the first-run setup screen: a QR code linking to the config URL."""
        url = self._config_url()
        qr = self._get_qr_surface(url, int(min(screen_width, screen_height) * 0.55))

        title_font = self._make_font(min(int(screen_height * 0.09), 44))
        url_font = self._make_font(min(int(screen_height * 0.06), 30))
        title = self.render_text_with_outline("Scan to set up", title_font, (255, 255, 255), (0, 0, 0), 2)
        url_surface = self.render_text_with_outline(url, url_font, (130, 200, 255), (0, 0, 0), 2)

        gap = int(screen_height * 0.03)
        qr_h = qr.get_height() if qr else 0
        total = title.get_height() + gap + qr_h + gap + url_surface.get_height()
        cx, _ = self.apply_display_offset(screen_width // 2, 0)
        y = (screen_height - total) // 2

        _, ty = self.apply_display_offset(0, y)
        self.screen.blit(title, title.get_rect(centerx=cx, top=ty))
        y += title.get_height() + gap
        if qr:
            _, qy = self.apply_display_offset(0, y)
            self.screen.blit(qr, qr.get_rect(centerx=cx, top=qy))
            y += qr_h + gap
        _, uy = self.apply_display_offset(0, y)
        self.screen.blit(url_surface, url_surface.get_rect(centerx=cx, top=uy))

    def draw_window(self):
        """Draw the window contents."""
        if not pygame.display.get_init():
            return

        screen_width = pygame.display.get_surface().get_width()
        screen_height = pygame.display.get_surface().get_height()

        self.screen.fill((0, 0, 0))

        current_time = time.time()

        # First-run setup splash: show the config QR code for a short window at boot.
        if current_time < self._setup_splash_until:
            self.draw_setup_screen(screen_width, screen_height)
            self.draw_notification()
            pygame.display.flip()
            return

        if self._should_show_text_interrupt(current_time):
            if not self.text_interrupt_showing:
                self._start_next_text_interrupt(current_time)
        else:
            self._stop_text_interrupt()

        text_interrupt_showing = self.text_interrupt_showing and self.active_text_interrupt

        if self.current_background is not None and not text_interrupt_showing:
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

        if self.last_identified and self.last_song_time and not text_interrupt_showing:
            # Always display the song info over the album art for the duration
            # of the track, rather than fading it out shortly after it starts.
            alpha = 255

            if alpha > 0:
                title_font = self._make_font(72)
                artist_font = self._make_font(48)

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

        if text_interrupt_showing:
            self.draw_text_interrupt(screen_width, screen_height, self.active_text_interrupt)
        elif not self.last_identified:
            self.draw_schedule_interrupt(screen_width, screen_height)

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
            self.font = self._make_font(36)
            pygame.mouse.set_visible(False)

            # Start config update task if enabled
            if self.config.get('remote', {}).get('enabled', False):
                self.config_update_task = asyncio.create_task(self._update_config_loop())
                self.logger.debug("Config update loop started")
            else:
                self.config_update_task = None

            # Start the LAN web server so the gist maintainer can force a refresh
            await self._start_web_server()

            # Show the setup QR splash for a short window at startup.
            try:
                splash_secs = int(os.environ.get('AL_SETUP_SPLASH_SECS', '30'))
            except (TypeError, ValueError):
                splash_secs = 30
            if splash_secs > 0:
                self._setup_splash_until = time.time() + splash_secs
                self.logger.info(f"Setup screen: {self._config_url()} (showing for {splash_secs}s)")

            while True:
                self.handle_events()

                # Throttle Sonos polling to ~1s so the faster (video) draw loop
                # does not hammer the speaker.
                now = time.time()
                if now - self._last_sonos_poll >= 1.0:
                    self._last_sonos_poll = now
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

                # Run at a higher frame rate while a video announcement is playing.
                active = self.active_text_interrupt
                playing_video = (self.text_interrupt_showing and active and active.get('type') == 'video')
                await asyncio.sleep(1 / 30 if playing_video else 0.1)

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

                title_font = self._make_font(36)
                message_font = self._make_font(24)

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

        font = self._make_font(font_size)

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

    # Display settings that are device-local and must survive remote config
    # updates: the custom font lives in the gitignored fonts/ folder, and
    # announcements may be configured per-device.
    LOCAL_DISPLAY_KEYS = ('font', 'announcements')

    def _merge_local_display_settings(self, remote_config):
        """Overlay device-local display settings onto a fetched remote config.

        The remote gist is authoritative for shared settings (schedule, hours,
        off-hours message, etc.), but the keys in ``LOCAL_DISPLAY_KEYS`` are
        device-local and are preserved across remote updates instead of being
        overwritten.
        """
        merged = dict(remote_config) if remote_config else {}
        local_display = (self.config or {}).get('display') or {}
        merged_display = dict(merged.get('display') or {})

        for key in self.LOCAL_DISPLAY_KEYS:
            if key in local_display:
                merged_display[key] = local_display[key]

        if merged_display:
            merged['display'] = merged_display
        return merged

    def _github_auth_headers(self):
        """Build GitHub API auth headers from a personal access token, if available.

        The token is read from the ``GITHUB_TOKEN`` (or ``GH_TOKEN``) environment
        variable, which is loaded from the gitignored ``.env`` file by the
        justfile/systemd service. Authenticating raises the GitHub API rate limit
        from 60 to 5000 requests per hour. Returns an empty dict when no token is
        set so requests stay unauthenticated.
        """
        token = os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN')
        if not token:
            return {}
        return {
            'Authorization': f'Bearer {token.strip()}',
            'Accept': 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28',
        }

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

                # Authenticate the GitHub API call (when a token is configured) to
                # avoid the 60 req/hr unauthenticated rate limit.
                auth_headers = self._github_auth_headers()
                if auth_headers:
                    self.logger.debug("Using authenticated GitHub API request")

                async with aiohttp.ClientSession() as session:
                    async with session.get(api_url, headers=auth_headers) as response:
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
                            remaining = response.headers.get('X-RateLimit-Remaining')
                            if response.status in (403, 429) and remaining == '0':
                                self.logger.error(
                                    "GitHub API rate limit exceeded. Set a GITHUB_TOKEN in "
                                    ".env to raise the limit from 60 to 5000 requests/hour."
                                )
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

    async def _refresh_config_from_remote(self):
        """Fetch the remote gist and apply it locally, preserving device-local
        display settings.

        Shared by the periodic update loop and the manual refresh endpoint.
        Returns a dict ``{ok, changed, message}`` describing the outcome. The
        running app reads config live at render time, so an applied change takes
        effect without a restart (the exception is ``display.font``, which is
        loaded once at startup).
        """
        remote_config = await self._fetch_remote_config()
        if not remote_config:
            return {
                'ok': False,
                'changed': False,
                'message': 'No config received (remote disabled, unreachable, or rate-limited)',
            }

        remote_config = self._merge_local_display_settings(remote_config)

        with self.config_lock:
            if yaml.dump(remote_config, sort_keys=True) == yaml.dump(self.config, sort_keys=True):
                return {'ok': True, 'changed': False, 'message': 'Already up to date'}

            if self._save_config(remote_config):
                self.logger.info("Updated config file from remote source")
                self.show_notification("Config Updated", "Configuration refreshed from remote")
                return {'ok': True, 'changed': True, 'message': 'Config updated'}

            self.logger.warning("Failed to save updated config to file")
            self.show_notification("Config Update Warning", "Failed to save remote config locally")
            return {'ok': False, 'changed': False, 'message': 'Failed to save config locally'}

    DAYS_OF_WEEK = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')

    # Config editor web app served on the LAN. Plain string (not an f-string)
    # because the embedded CSS/JS contains literal braces.
    _CONFIG_EDITOR_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>Feature Flora — Config</title>
<link rel="icon" type="image/png" href="/favicon-96x96.png" sizes="96x96" />
<link rel="icon" type="image/svg+xml" href="/favicon.svg" />
<link rel="shortcut icon" href="/favicon.ico" />
<link rel="apple-touch-icon" sizes="180x180" href="/apple-touch-icon.png" />
<meta name="apple-mobile-web-app-title" content="TV Config" />
<link rel="manifest" href="/site.webmanifest" />
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { margin:0; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
         background:#0f0f0f; color:#f4f4f4; padding:0 0 120px; -webkit-text-size-adjust:100%; }
  .page { max-width:560px; margin:0 auto; padding:20px 18px; }
  h1 { font-weight:700; font-size:1.6rem; margin:8px 0 4px; }
  p.sub { margin:0 0 18px; opacity:.6; font-size:.95rem; }
  section, details { background:#1a1a1a; border:1px solid #2a2a2a; border-radius:16px;
                     padding:16px; margin:14px 0; }
  h2 { font-size:1.05rem; margin:0 0 12px; font-weight:600; }
  summary { font-size:1.05rem; font-weight:600; cursor:pointer; }
  details[open] summary { margin-bottom:12px; }
  .field { display:flex; flex-direction:column; gap:6px; margin:10px 0; }
  .field > span { font-size:.85rem; opacity:.7; }
  input[type=text], input[type=number], textarea, input[type=time] {
    width:100%; font-size:1.05rem; padding:12px; border-radius:12px;
    border:1px solid #333; background:#222; color:#fff; font-family:inherit; }
  textarea { resize:vertical; }
  .dayrow { display:flex; align-items:center; justify-content:space-between; gap:10px;
            padding:10px 0; border-bottom:1px solid #242424; }
  .dayrow:last-child { border-bottom:none; }
  .daytoggle { display:flex; align-items:center; gap:10px; font-size:1.05rem; min-width:120px; }
  .daytoggle input { width:22px; height:22px; }
  .times { display:flex; align-items:center; gap:8px; }
  .times input[type=time] { width:auto; padding:8px 10px; }
  .times .dash { opacity:.5; }
  .times .closed { display:none; opacity:.5; }
  .dayrow.isclosed .times .open, .dayrow.isclosed .times .close, .dayrow.isclosed .times .dash { display:none; }
  .dayrow.isclosed .times .closed { display:inline; }
  .anncard { border:1px solid #2c2c2c; border-radius:12px; padding:12px; margin:10px 0;
             display:flex; flex-direction:column; gap:8px; }
  .imgrow { display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
  .imgbtn { background:#333; color:#fff; padding:9px 14px; border-radius:10px; font-size:.9rem;
            cursor:pointer; display:inline-block; }
  .rmimg { background:#5a2d2d; padding:8px 12px; font-size:.85rem; }
  .imgprev, .vidprev { max-width:100%; max-height:200px; border-radius:10px; margin-top:4px; display:block; }
  .imgstatus { font-size:.85rem; }
  .imghint { font-size:.8rem; opacity:.55; }
  .anncard.hasmedia .anntitle, .anncard.hasmedia .annmsg { opacity:.45; }
  button { font-size:1.05rem; font-weight:600; padding:12px 18px; border:none; border-radius:12px;
           background:#2e7d32; color:#fff; cursor:pointer; -webkit-tap-highlight-color:transparent;
           touch-action:manipulation; }
  button:active { transform:scale(.98); }
  button.secondary { background:#333; }
  button.remove { background:#5a2d2d; align-self:flex-end; padding:8px 14px; font-size:.9rem; }
  .savebar { position:fixed; left:0; right:0; bottom:0; background:#0f0f0fee;
             backdrop-filter:blur(8px); border-top:1px solid #2a2a2a; padding:14px 18px;
             display:flex; align-items:center; gap:14px; }
  .savebar button { flex:0 0 auto; min-width:140px; font-size:1.2rem; padding:16px 28px; }
  #status { font-size:1rem; }
  .ok { color:#81c784; } .err { color:#e57373; } .muted { opacity:.7; }
</style>
</head>
<body>
<div class="page">
  <h1>Feature Flora</h1>
  <p class="sub">Edit the display settings and tap Save. Changes appear on the screen right away.</p>

  <section>
    <h2>Weekly Hours</h2>
    <div id="schedule"></div>
  </section>

  <section>
    <h2>Messages</h2>
    <label class="field"><span>Schedule header</span>
      <input id="header" type="text" placeholder="Open This Week"></label>
    <label class="field"><span>Closed message</span>
      <textarea id="offhours" rows="2" placeholder="We're currently closed."></textarea></label>
  </section>

  <section>
    <h2>Announcements</h2>
    <div id="annlist"></div>
    <button type="button" id="addann" class="secondary">+ Add announcement</button>
  </section>

  <details>
    <summary>Advanced timing</summary>
    <label class="field"><span>Rotate every (seconds)</span>
      <input id="interval" type="number" min="5" value="60"></label>
    <label class="field"><span>Show each for (seconds)</span>
      <input id="duration" type="number" min="1" value="10"></label>
  </details>
</div>

<div class="savebar">
  <button id="save">Save</button>
  <div id="status" class="muted">Loading.</div>
</div>

<script>
const DAYS = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"];
let CONFIG = {};

function to24(label){
  if(!label) return "";
  const m = String(label).trim().match(/^(\\d{1,2}):(\\d{2})\\s*([AaPp][Mm])?$/);
  if(!m) return "";
  let h = parseInt(m[1],10); const min = m[2];
  const ap = m[3] ? m[3].toUpperCase() : null;
  if(ap){ if(ap==="PM" && h<12) h+=12; if(ap==="AM" && h===12) h=0; }
  return String(h).padStart(2,"0")+":"+min;
}
function to12(hhmm){
  if(!hhmm) return "";
  const m = String(hhmm).match(/^(\\d{1,2}):(\\d{2})$/);
  if(!m) return hhmm;
  let h = parseInt(m[1],10); const min = m[2];
  const ap = h>=12 ? "PM" : "AM";
  let h12 = h%12; if(h12===0) h12=12;
  return h12+":"+min+" "+ap;
}
function esc(s){ return String(s||"").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/"/g,"&quot;"); }

async function load(){
  try {
    const r = await fetch("/config");
    CONFIG = await r.json();
  } catch(e){ setStatus(false,"Could not load config"); return; }
  renderSchedule();
  const d = CONFIG.display || {};
  document.getElementById("header").value = d.schedule_header || "";
  document.getElementById("offhours").value = d.off_hours_message || "";
  document.getElementById("interval").value = (d.schedule_interval != null) ? d.schedule_interval : 60;
  document.getElementById("duration").value = (d.schedule_duration != null) ? d.schedule_duration : 10;
  renderAnnouncements(d.announcements || []);
  setStatus(true,"Ready"); document.getElementById("status").className = "muted";
}

function renderSchedule(){
  const byDay = {};
  (CONFIG.schedule || []).forEach(s => { if(s && s.day) byDay[s.day] = s; });
  const wrap = document.getElementById("schedule");
  wrap.innerHTML = "";
  DAYS.forEach(day => {
    const entry = byDay[day]; const open = !!entry;
    const row = document.createElement("div");
    row.className = "dayrow";
    row.innerHTML =
      '<label class="daytoggle"><input type="checkbox" data-day="'+day+'" class="dayopen" '+(open?"checked":"")+'><span>'+day+'</span></label>'+
      '<div class="times">'+
        '<input type="time" class="open" value="'+(open?to24(entry.open):"09:00")+'">'+
        '<span class="dash">to</span>'+
        '<input type="time" class="close" value="'+(open?to24(entry.close):"17:00")+'">'+
        '<span class="closed">Closed</span>'+
      '</div>';
    wrap.appendChild(row);
    const cb = row.querySelector(".dayopen");
    const sync = () => row.classList.toggle("isclosed", !cb.checked);
    cb.addEventListener("change", sync); sync();
  });
}

function addAnnouncement(title, message, image, video){
  const card = document.createElement("div");
  card.className = "anncard";
  card.innerHTML =
    '<input class="anntitle" type="text" placeholder="Title (optional)" value="'+esc(title)+'">'+
    '<textarea class="annmsg" rows="2" placeholder="Message">'+esc(message)+'</textarea>'+
    '<div class="imgrow">'+
      '<label class="imgbtn">Add image / video<input type="file" accept="image/*,video/*" class="imgfile" hidden></label>'+
      '<button type="button" class="rmimg" hidden>Remove media</button>'+
      '<span class="imgstatus muted"></span>'+
    '</div>'+
    '<span class="imghint" hidden>Shown fit to the display, with the title/message above it.</span>'+
    '<img class="imgprev" hidden alt="">'+
    '<video class="vidprev" hidden muted playsinline loop controls></video>'+
    '<button type="button" class="remove">Remove</button>';
  const imgPrev = card.querySelector(".imgprev");
  const vidPrev = card.querySelector(".vidprev");
  const rmimg = card.querySelector(".rmimg");
  const hint = card.querySelector(".imghint");
  const status = card.querySelector(".imgstatus");
  function showMedia(name, kind){
    card.dataset.image = (kind === "image") ? (name || "") : "";
    card.dataset.video = (kind === "video") ? (name || "") : "";
    const src = name ? ("/uploads/"+encodeURIComponent(name)+"?t="+Date.now()) : "";
    if(name && kind === "image"){
      imgPrev.src = src; imgPrev.hidden = false; vidPrev.hidden = true; vidPrev.removeAttribute("src");
    } else if(name && kind === "video"){
      vidPrev.src = src; vidPrev.hidden = false; imgPrev.hidden = true; imgPrev.removeAttribute("src");
    } else {
      imgPrev.hidden = true; vidPrev.hidden = true;
      imgPrev.removeAttribute("src"); vidPrev.removeAttribute("src");
    }
    const has = !!name;
    rmimg.hidden = !has; hint.hidden = !has;
    card.classList.toggle("hasmedia", has);
    if(has) status.textContent = "";
  }
  card.querySelector(".imgfile").addEventListener("change", async (e)=>{
    const file = e.target.files[0]; if(!file) return;
    status.className = "imgstatus muted"; status.textContent = "Uploading.";
    const fd = new FormData(); fd.append("media", file);
    try {
      const r = await fetch("/upload", { method:"POST", body: fd });
      const d = await r.json();
      if(d.ok){ showMedia(d.filename, d.kind); }
      else { status.className="imgstatus err"; status.textContent = "\\u2717 "+(d.message||"Upload failed"); }
    } catch(err){ status.className="imgstatus err"; status.textContent="\\u2717 Upload failed"; }
    e.target.value = "";
  });
  rmimg.addEventListener("click", ()=>showMedia("", ""));
  card.querySelector(".remove").addEventListener("click", ()=>card.remove());
  document.getElementById("annlist").appendChild(card);
  if(video) showMedia(video, "video");
  else if(image) showMedia(image, "image");
  else showMedia("", "");
}
function renderAnnouncements(anns){
  document.getElementById("annlist").innerHTML = "";
  (anns||[]).forEach(a => addAnnouncement(a.title, a.message || (a.lines ? a.lines.join("\\n") : ""), a.image, a.video));
}

function collect(){
  const schedule = [];
  document.querySelectorAll(".dayrow").forEach(row => {
    const cb = row.querySelector(".dayopen");
    if(cb.checked){
      schedule.push({ day: cb.dataset.day,
        open: to12(row.querySelector(".open").value),
        close: to12(row.querySelector(".close").value) });
    }
  });
  const announcements = [];
  document.querySelectorAll(".anncard").forEach(card => {
    const title = card.querySelector(".anntitle").value.trim();
    const message = card.querySelector(".annmsg").value.trim();
    const image = card.dataset.image || "";
    const video = card.dataset.video || "";
    if(title || message || image || video) announcements.push({title, message, image, video});
  });
  return { schedule, display: {
    schedule_header: document.getElementById("header").value,
    off_hours_message: document.getElementById("offhours").value,
    schedule_interval: parseInt(document.getElementById("interval").value,10) || 60,
    schedule_duration: parseInt(document.getElementById("duration").value,10) || 10,
    announcements } };
}

function setStatus(ok, msg){
  const s = document.getElementById("status");
  s.className = ok ? "ok" : "err";
  s.textContent = (ok ? "\\u2713 " : "\\u2717 ") + msg;
}

async function save(){
  const btn = document.getElementById("save");
  btn.disabled = true;
  document.getElementById("status").className = "muted";
  document.getElementById("status").textContent = "Saving.";
  try {
    const r = await fetch("/config", { method:"POST",
      headers:{"Content-Type":"application/json"}, body: JSON.stringify(collect()) });
    const d = await r.json();
    setStatus(!!d.ok, d.message || (d.ok?"Saved":"Error"));
  } catch(e){ setStatus(false,"Could not reach the display"); }
  finally { btn.disabled = false; }
}

document.getElementById("addann").addEventListener("click", ()=>addAnnouncement("",""));
document.getElementById("save").addEventListener("click", save);
load();
</script>
</body>
</html>"""

    async def _handle_editor_index(self, request):
        """Serve the config editor web app."""
        from aiohttp import web
        return web.Response(text=self._CONFIG_EDITOR_HTML, content_type='text/html')

    async def _handle_config_get(self, request):
        """Return the current config as JSON for the editor to populate its form."""
        from aiohttp import web
        with self.config_lock:
            cfg = copy.deepcopy(self.config) if self.config else {}
        return web.json_response(cfg)

    async def _handle_config_post(self, request):
        """Apply edited config from the web app: validate, merge, save, live-reload."""
        from aiohttp import web
        try:
            data = await request.json()
        except Exception:
            return web.json_response({'ok': False, 'message': 'Invalid request'}, status=400)
        result = self._apply_editor_config(data)
        return web.json_response(result, status=(200 if result['ok'] else 400))

    ALLOWED_IMAGE_EXTS = ('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp')
    ALLOWED_VIDEO_EXTS = ('.mp4', '.mov', '.m4v', '.webm')
    MAX_IMAGE_BYTES = 15 * 1024 * 1024   # 15 MB
    MAX_VIDEO_BYTES = 300 * 1024 * 1024  # 300 MB

    def _video_target_dim(self):
        """Max dimension to downscale uploaded videos to: the display's larger
        side (so frames are near-native and cheap to decode/scale), overridable
        with AL_VIDEO_MAX_DIM."""
        target = 1280
        try:
            surface = pygame.display.get_surface()
            if surface:
                target = max(surface.get_size())
        except Exception:
            pass
        try:
            target = int(os.environ.get('AL_VIDEO_MAX_DIM', target))
        except (TypeError, ValueError):
            pass
        return max(160, target)

    async def _transcode_video(self, src_path, dest_path):
        """Downscale/normalize a video with ffmpeg for smooth playback: cap the
        long side to the display size, bake in rotation, drop audio, re-encode to
        H.264. Returns True on success (requires ffmpeg)."""
        dim = self._video_target_dim()
        vf = f"scale={dim}:{dim}:force_original_aspect_ratio=decrease:force_divisible_by=2"
        cmd = ['ffmpeg', '-y', '-i', src_path, '-vf', vf, '-r', '30', '-an',
               '-c:v', 'libx264', '-preset', 'veryfast', '-pix_fmt', 'yuv420p',
               '-movflags', '+faststart', dest_path]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
            _, stderr = await proc.communicate()
            if proc.returncode == 0 and os.path.exists(dest_path) and os.path.getsize(dest_path) > 0:
                self.logger.info(f"Transcoded video to max dim {dim}: {os.path.basename(dest_path)}")
                return True
            self.logger.error(f"ffmpeg transcode failed (rc={proc.returncode}): "
                              f"{stderr.decode(errors='ignore')[-400:]}")
        except FileNotFoundError:
            self.logger.warning("ffmpeg not found; storing original video without downscaling")
        except Exception as e:
            self.logger.error(f"Video transcode error: {e}")
        return False

    async def _stream_upload_to(self, field, path, max_bytes):
        """Stream a multipart field to a file with a size cap. Returns the byte
        count, or None if it exceeded the cap (partial file removed)."""
        size = 0
        with open(path, 'wb') as f:
            while True:
                chunk = await field.read_chunk()
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    f.close()
                    os.remove(path)
                    return None
                f.write(chunk)
        return size

    async def _handle_upload(self, request):
        """Receive an announcement image or video (multipart), save it to
        announcements/. Videos are downscaled with ffmpeg for smooth playback."""
        from aiohttp import web
        try:
            reader = await request.multipart()
        except Exception:
            return web.json_response({'ok': False, 'message': 'Invalid upload'}, status=400)

        field = await reader.next()
        while field is not None and field.name != 'media':
            field = await reader.next()
        if field is None:
            return web.json_response({'ok': False, 'message': 'No file provided'}, status=400)

        ext = os.path.splitext(field.filename or '')[1].lower()
        if ext in self.ALLOWED_IMAGE_EXTS:
            kind, max_bytes = 'image', self.MAX_IMAGE_BYTES
        elif ext in self.ALLOWED_VIDEO_EXTS:
            kind, max_bytes = 'video', self.MAX_VIDEO_BYTES
        else:
            allowed = ', '.join(self.ALLOWED_IMAGE_EXTS + self.ALLOWED_VIDEO_EXTS)
            return web.json_response({'ok': False, 'message': f'Unsupported type (use {allowed})'}, status=400)

        base = re.sub(r'[^A-Za-z0-9_-]', '_', os.path.splitext(os.path.basename(field.filename or ''))[0])[:40] or kind
        directory = self._announcements_dir()
        too_large = web.json_response(
            {'ok': False, 'message': f'{kind.title()} too large (max {max_bytes // (1024 * 1024)} MB)'}, status=400)

        try:
            if kind == 'image':
                filename = f"{base}-{uuid.uuid4().hex[:8]}{ext}"
                if await self._stream_upload_to(field, os.path.join(directory, filename), max_bytes) is None:
                    return too_large
            else:
                raw = os.path.join(directory, f"{base}-{uuid.uuid4().hex[:8]}.upload{ext}")
                if await self._stream_upload_to(field, raw, max_bytes) is None:
                    return too_large
                final_name = f"{base}-{uuid.uuid4().hex[:8]}.mp4"
                if await self._transcode_video(raw, os.path.join(directory, final_name)):
                    try:
                        os.remove(raw)
                    except OSError:
                        pass
                    filename = final_name
                else:
                    filename = os.path.basename(raw)  # fall back to the original upload
        except Exception as e:
            self.logger.error(f"Failed to save uploaded {kind}: {e}")
            return web.json_response({'ok': False, 'message': f'Failed to save {kind}'}, status=500)

        self.logger.info(f"Uploaded announcement {kind}: {filename}")
        return web.json_response({'ok': True, 'filename': filename, 'kind': kind})

    async def _handle_upload_get(self, request):
        """Serve an uploaded announcement image (for editor previews)."""
        from aiohttp import web
        name = os.path.basename(request.match_info.get('name', ''))
        path = os.path.join(self._announcements_dir(), name)
        if not name or not os.path.exists(path):
            return web.Response(status=404)
        return web.FileResponse(path)

    # Root-served static assets (favicon set + web manifest).
    STATIC_FILES = (
        'favicon.svg', 'favicon-96x96.png', 'favicon.ico', 'apple-touch-icon.png',
        'web-app-manifest-192x192.png', 'web-app-manifest-512x512.png', 'site.webmanifest',
    )

    def _static_dir(self):
        """Folder of publicly served static files (favicons, web manifest)."""
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')

    async def _handle_static_file(self, request):
        """Serve a known static asset from the static/ folder by exact name."""
        from aiohttp import web
        name = os.path.basename(request.path)
        if name not in self.STATIC_FILES:
            return web.Response(status=404)
        path = os.path.join(self._static_dir(), name)
        if not os.path.exists(path):
            return web.Response(status=404)
        return web.FileResponse(path)

    async def _handle_refresh_action(self, request):
        """Optional: force a one-off config pull from the remote gist (if enabled)."""
        from aiohttp import web
        self.logger.info("Manual config refresh requested via web")
        result = await self._refresh_config_from_remote()
        return web.json_response(result)

    def _apply_editor_config(self, data):
        """Validate editor form data, merge it into the current config (preserving
        keys the editor does not manage, e.g. display.font and the remote section),
        save, and live-reload. The running app reads config at render time, so
        schedule/message/announcement changes take effect without a restart."""
        if not isinstance(data, dict):
            return {'ok': False, 'message': 'Invalid payload'}

        schedule_in = data.get('schedule', [])
        if not isinstance(schedule_in, list):
            return {'ok': False, 'message': 'Schedule must be a list'}
        valid_days = set(self.DAYS_OF_WEEK)
        clean_schedule = []
        for entry in schedule_in:
            if not isinstance(entry, dict):
                return {'ok': False, 'message': 'Invalid schedule entry'}
            day = str(entry.get('day', '')).strip()
            open_t = str(entry.get('open', '')).strip()
            close_t = str(entry.get('close', '')).strip()
            if day not in valid_days:
                return {'ok': False, 'message': f'Invalid day: {day or "(blank)"}'}
            if not open_t or not close_t:
                return {'ok': False, 'message': f'{day}: open and close times are required'}
            clean_schedule.append({'day': day, 'open': open_t, 'close': close_t})

        disp_in = data.get('display', {})
        if not isinstance(disp_in, dict):
            return {'ok': False, 'message': 'Display settings must be an object'}

        clean_anns = []
        for ann in disp_in.get('announcements', []) or []:
            if not isinstance(ann, dict):
                return {'ok': False, 'message': 'Invalid announcement'}
            title = str(ann.get('title', '')).strip()
            message = str(ann.get('message', '')).strip()
            image = os.path.basename(str(ann.get('image', '')).strip()) if ann.get('image') else ''
            video = os.path.basename(str(ann.get('video', '')).strip()) if ann.get('video') else ''
            if image or video or title or message:
                entry = {'title': title, 'message': message}
                if video:
                    entry['video'] = video
                elif image:
                    entry['image'] = image
                clean_anns.append(entry)

        def as_number(value, default):
            try:
                return type(default)(value)
            except (TypeError, ValueError):
                return default

        with self.config_lock:
            new_config = copy.deepcopy(self.config) if self.config else {}
            new_config['schedule'] = clean_schedule

            display = dict(new_config.get('display') or {})
            if 'schedule_header' in disp_in:
                display['schedule_header'] = str(disp_in['schedule_header'])
            if 'off_hours_message' in disp_in:
                display['off_hours_message'] = str(disp_in['off_hours_message'])
            if 'schedule_interval' in disp_in:
                display['schedule_interval'] = as_number(disp_in['schedule_interval'], display.get('schedule_interval', 60))
            if 'schedule_duration' in disp_in:
                display['schedule_duration'] = as_number(disp_in['schedule_duration'], display.get('schedule_duration', 10))
            display['announcements'] = clean_anns
            new_config['display'] = display

            if self._save_config(new_config):
                self.logger.info("Config updated via web editor")
                self.show_notification("Config Updated", "Configuration updated from web app")
                return {'ok': True, 'message': 'Saved'}

        return {'ok': False, 'message': 'Failed to save config'}

    async def _start_web_server(self):
        """Start the LAN config-editor web server.

        Lets the display be configured from a phone or tablet on the same network
        instead of editing YAML. The port can be overridden with the AL_WEB_PORT
        environment variable (default 8080).
        """
        try:
            from aiohttp import web
            app = web.Application()
            app.router.add_get('/', self._handle_editor_index)
            app.router.add_get('/config', self._handle_config_get)
            app.router.add_post('/config', self._handle_config_post)
            app.router.add_post('/upload', self._handle_upload)
            app.router.add_get('/uploads/{name}', self._handle_upload_get)
            app.router.add_post('/refresh', self._handle_refresh_action)
            for static_name in self.STATIC_FILES:
                app.router.add_get('/' + static_name, self._handle_static_file)

            runner = web.AppRunner(app)
            await runner.setup()
            port = int(os.environ.get('AL_WEB_PORT', '8080'))
            site = web.TCPSite(runner, '0.0.0.0', port)
            await site.start()
            self._web_runner = runner
            self.logger.info(f"Config editor web server running on http://0.0.0.0:{port}")
        except Exception as e:
            self.logger.error(f"Failed to start config editor web server: {e}")

    async def _update_config_loop(self):
        """Periodically update config from remote source."""
        self.logger.debug("Starting config update loop")
        await asyncio.sleep(2)

        while True:
            try:
                self.logger.debug("Checking for remote config updates...")
                result = await self._refresh_config_from_remote()
                self.logger.debug(f"Config refresh: {result['message']}")

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
