import pyaudio
import numpy as np
from shazamio import Shazam, Serialize, HTTPClient
from aiohttp_retry import ExponentialRetry
import asyncio
import pygame
import requests
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
from pydub import AudioSegment

# Suppress urllib3 warnings
warnings.filterwarnings('ignore', category=Warning)

class MusicIdentifier:
    def __init__(self, debug_mode=False, device_index=None, always_open=False):
        self.debug_mode = debug_mode
        self.device_index = device_index
        self.always_open = always_open
        self.start_time = time.time()
        
        # Set up logging
        log_level = logging.DEBUG if debug_mode else logging.INFO
        logging.basicConfig(
            level=log_level,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )
        self.logger = logging.getLogger(__name__)
        self.logger.debug("Initializing MusicIdentifier in debug mode" if debug_mode else "Initializing MusicIdentifier")
        
        # Audio parameters
        self.FORMAT = pyaudio.paInt16  # Use int16 format which matches Shazam's requirements
        self.CHANNELS = 1  # Mono audio
        self.RATE = 16000  # Initial rate, will be updated based on device
        self.CHUNK = 2048  # Larger chunks for better performance
        
        # Initialize PyAudio first
        self.p = pyaudio.PyAudio()
        
        # Initialize audio with error handling
        try:
            # Use existing device selection logic
            self.input_device_index = self._find_input_device(device_index)
            if self.input_device_index is None:
                raise RuntimeError("No suitable input device found")
            
            device_info = self.p.get_device_info_by_index(self.input_device_index)
            self.logger.info(f"Using input device: {device_info['name']} (index: {self.input_device_index})")
            
        except Exception as e:
            self.logger.error(f"Error initializing audio: {str(e)}")
            raise
        
        # Load config
        self.config = self._load_config()
        self.config_lock = threading.Lock()
        self.last_config_update = time.time()
        
        # Start config update task if enabled
        if self.config.get('remote', {}).get('enabled', False):
            self.config_update_task = asyncio.create_task(self._update_config_loop())
            self.logger.debug("Config update loop started")
        else:
            self.config_update_task = None
        
        # Create debug output directory
        if debug_mode:
            import os
            self.debug_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'debug_output')
            os.makedirs(self.debug_dir, exist_ok=True)
        
        # Initialize Shazam with retry options
        self.shazam = Shazam(
            http_client=HTTPClient(
                retry_options=ExponentialRetry(
                    attempts=12,
                    max_timeout=204.8,
                    statuses={500, 502, 503, 504, 429}
                ),
            ),
        )
        
        # Initialize PyGame display
        pygame.init()
        self.screen_width = 800
        self.screen_height = 600
        self.screen = None
        self.font = None
        self.is_fullscreen = False
        self.is_stretched = False  # New flag for stretch mode
        
        # Text scrolling
        self.scroll_start_time = 0
        self.SCROLL_PAUSE = 2.0  # Pause at the start and end for 2 seconds
        self.SCROLL_SPEED = 100  # Pixels per second
        
        # Display modes
        self.NORMAL_RATIO = 16/9  # 1080p ratio
        self.CRT_RATIO = 4/3      # CRT ratio
        self.stretch_factor = 1.0  # Will be updated when stretch mode is toggled
        
        # Colors
        self.BACKGROUND_COLOR = (0, 0, 0)  # Black
        self.TEXT_COLOR = (255, 255, 255)  # White
        
        # Song tracking
        self.last_identified = None
        self.last_song_time = None  # Track when the song was identified
        self.fade_duration = 1.0  # Fade out over 1 second
        self.show_duration = 5.0  # Show title for 5 seconds
        self.current_background = None
        self.permanent_schedule = False  # New flag for permanent schedule display
        
        # Schedule display timing
        self.last_schedule_display = 0
        self.schedule_showing = False
        self.schedule_show_start = 0

        # Screensaver state
        self.screensaver_pos = [100, 100]  # Initial position
        self.screensaver_velocity = [2, 2]  # Movement speed and direction
        self.screensaver_last_update = time.time()
        self.screensaver_color = (255, 255, 255)  # Initial color
        self.screensaver_color_direction = [1, 1, 1]  # Color change direction for RGB

    def _find_input_device(self, device_index=None):
        """Find the audio input device to use."""
        input_devices = []
        default_index = None
        
        try:
            default_host = self.p.get_default_host_api_info()
            default_index = default_host.get('defaultInputDevice')
        except OSError:
            self.logger.warning("Could not get default input device")
        
        # List all devices and find input devices
        for i in range(self.p.get_device_count()):
            try:
                dev_info = self.p.get_device_info_by_index(i)
                if dev_info['maxInputChannels'] > 0:  # If it has input channels
                    input_devices.append((i, dev_info))
                    self.logger.debug(f"Found input device {i}: {dev_info['name']}")
                    # Print supported sample rates if in debug mode
                    if self.debug_mode:
                        try:
                            supported_rates = [
                                rate for rate in [8000, 11025, 16000, 22050, 44100, 48000, 96000]
                                if self._is_rate_supported(i, rate)
                            ]
                            self.logger.debug(f"Supported rates for device {i}: {supported_rates}")
                        except Exception as e:
                            self.logger.debug(f"Could not check rates for device {i}: {e}")
            except Exception as e:
                self.logger.warning(f"Error getting device info for index {i}: {e}")
                continue

        if not input_devices:
            self.logger.error("No input devices found")
            sys.exit(1)

        # If a specific device was requested, verify it
        if device_index is not None:
            if not any(idx == device_index for idx, _ in input_devices):
                self.logger.error(f"Selected device index {self.device_index} is not valid")
                sys.exit(1)
            # Get supported rates for the selected device
            self.RATE = self._get_best_rate(device_index)
            return device_index

        # If there's only one device, use it automatically
        if len(input_devices) == 1:
            dev_idx, dev_info = input_devices[0]
            print(f"\nAutomatically selecting the only available device: {dev_info['name']}")
            self.RATE = self._get_best_rate(dev_idx)
            return dev_idx

        # If we have a default device and no specific device was requested, use it
        if default_index is not None:
            for original_idx, dev_info in input_devices:
                if original_idx == default_index:
                    print(f"\nUsing default input device: {dev_info['name']}")
                    self.RATE = self._get_best_rate(original_idx)
                    return original_idx

        # Interactive device selection
        while True:
            print("\nAvailable input devices:")
            for original_idx, dev_info in input_devices:
                is_default = original_idx == default_index
                print(f"{original_idx}: {dev_info['name']}{' (default)' if is_default else ''}")

            try:
                selection = input("\nSelect input device (number or Enter for default): ").strip()
                if not selection and default_index is not None:
                    # Use default device if Enter is pressed
                    for original_idx, dev_info in input_devices:
                        if original_idx == default_index:
                            print(f"Using default device: {dev_info['name']}")
                            self.RATE = self._get_best_rate(original_idx)
                            return original_idx

                if not selection:
                    continue

                device_index = int(selection)
                # Look up the device by its original index
                for original_idx, dev_info in input_devices:
                    if original_idx == device_index:
                        self.logger.info(f"Using device {device_index}: {dev_info['name']}")
                        self.RATE = self._get_best_rate(device_index)
                        return device_index
                print("Invalid selection. Please try again.")
            except ValueError:
                print("Please enter a number or press Enter for default device.")
            except KeyboardInterrupt:
                print("\nExiting...")
                sys.exit(0)

    def _is_rate_supported(self, device_index, rate):
        """Check if a sample rate is supported by the device."""
        try:
            supported = self.p.is_format_supported(
                rate,
                input_device=device_index,
                input_channels=self.CHANNELS,
                input_format=self.FORMAT
            )
            return supported
        except Exception:
            return False

    def _get_best_rate(self, device_index):
        """Get the best supported sample rate for the device."""
        # Try common sample rates in order of preference
        preferred_rates = [16000, 44100, 48000, 22050, 11025, 8000]
        
        for rate in preferred_rates:
            if self._is_rate_supported(device_index, rate):
                self.logger.info(f"Using sample rate: {rate}")
                return rate
        
        # If none of our preferred rates work, get the default rate from the device
        try:
            dev_info = self.p.get_device_info_by_index(device_index)
            default_rate = int(dev_info.get('defaultSampleRate', 44100))
            if self._is_rate_supported(device_index, default_rate):
                self.logger.info(f"Using device default sample rate: {default_rate}")
                return default_rate
        except Exception as e:
            self.logger.warning(f"Error getting device default rate: {e}")
        
        # Fall back to 44100 if nothing else works
        self.logger.warning("Falling back to 44100 Hz")
        return 44100

    def list_devices():
        """List all available input devices without starting the program"""
        p = pyaudio.PyAudio()
        info = p.get_host_api_info_by_index(0)
        numdevices = info.get('deviceCount')
        
        print("\nAudio Host API:", p.get_default_host_api_info()['name'])
        print("\nAvailable input devices:")
        
        for i in range(numdevices):
            device_info = p.get_device_info_by_index(i)
            # Print all device info for debugging
            print(f"\nDevice {i}:")
            for key, value in device_info.items():
                print(f"  {key}: {value}")
            
            # Check if it's an input device
            if device_info.get('maxInputChannels') > 0:
                print(f"\n==> Input Device {i}: {device_info['name']}")
                print(f"    Default Sample Rate: {device_info['defaultSampleRate']}")
                print(f"    Max Input Channels: {device_info['maxInputChannels']}")
        
        # Print default input device
        try:
            default_input = p.get_default_input_device_info()
            print(f"\nDefault Input Device: {default_input['name']} (index: {default_input['index']})")
        except IOError:
            print("\nNo default input device found")
        
        p.terminate()

    def _load_config(self):
        """Load the configuration from YAML file."""
        config_dir = os.path.dirname(os.path.abspath(__file__))
        config_path = os.path.join(config_dir, 'config.yaml')
        sample_config_path = os.path.join(config_dir, 'config.sample.yaml')

        # If config.yaml doesn't exist but sample exists, create from sample
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
            # Create a backup of the old config
            if os.path.exists(config_path):
                backup_path = config_path + '.bak'
                try:
                    import shutil
                    shutil.copy2(config_path, backup_path)
                except Exception as e:
                    self.logger.warning(f"Failed to create config backup: {e}")

            # Write the new config with proper permissions
            with open(config_path, 'w') as f:
                yaml.dump(config, f, default_flow_style=False)
            
            # Set file permissions to be readable and writable
            try:
                os.chmod(config_path, 0o666)
            except Exception as e:
                self.logger.warning(f"Failed to set config file permissions: {e}")

            self.logger.debug("Config file updated successfully")
            
            # Reload the config immediately
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
            return True  # If no schedule is found, operate 24/7
            
        current_time = datetime.now()
        current_day = current_time.strftime("%A")
        
        # Find schedule for current day
        day_schedule = None
        for schedule_item in self.config['schedule']:
            if schedule_item['day'] == current_day:
                day_schedule = schedule_item
                break
        
        if not day_schedule:
            self.logger.debug(f"No schedule found for {current_day}, staying inactive")
            return False
            
        # Parse opening and closing times
        try:
            open_time = datetime.strptime(day_schedule['open'], "%I:%M %p").time()
            close_time = datetime.strptime(day_schedule['close'], "%I:%M %p").time()
            current_time = current_time.time()
            
            # Check if current time is within operating hours
            return open_time <= current_time <= close_time
        except ValueError as e:
            self.logger.error(f"Error parsing schedule times: {e}")
            return False

    def render_text_with_outline(self, text, font, color, outline_color=(0, 0, 0), outline_width=2):
        """Render text with an outline for better visibility."""
        # First render the outline
        outline_surfaces = []
        for dx in range(-outline_width, outline_width + 1):
            for dy in range(-outline_width, outline_width + 1):
                if dx*dx + dy*dy <= outline_width*outline_width:  # Circle shape for outline
                    outline_surface = font.render(text, True, outline_color)
                    outline_surfaces.append((outline_surface, (dx, dy)))
        
        # Then render the main text
        text_surface = font.render(text, True, color)
        
        # Create a surface to hold both outline and text
        width = text_surface.get_width() + outline_width * 2
        height = text_surface.get_height() + outline_width * 2
        final_surface = pygame.Surface((width, height), pygame.SRCALPHA)
        
        # Blit all outline surfaces
        for surface, (dx, dy) in outline_surfaces:
            final_surface.blit(surface, (dx + outline_width, dy + outline_width))
        
        # Blit the main text on top
        final_surface.blit(text_surface, (outline_width, outline_width))
        
        return final_surface

    def draw_scrolling_text(self, text_surface, y_pos, alpha, max_width):
        """Draw scrolling text if it's wider than the screen."""
        text_width = text_surface.get_width()
        
        if text_width <= max_width:
            # If text fits, center it precisely
            x_pos = (self.screen_width - text_width) // 2  # Center relative to screen width, not max_width
            # Create a temporary surface with alpha support
            temp_surface = pygame.Surface((text_width, text_surface.get_height()), pygame.SRCALPHA)
            temp_surface.blit(text_surface, (0, 0))
            # Apply alpha
            temp_surface.set_alpha(int(alpha * 255))
            self.screen.blit(temp_surface, (x_pos, y_pos))
        else:
            # Calculate scroll position
            current_time = time.time()
            elapsed = current_time - self.scroll_start_time
            total_scroll_time = (text_width / self.SCROLL_SPEED) + (self.SCROLL_PAUSE * 2)
            
            if elapsed > total_scroll_time:
                self.scroll_start_time = current_time
                elapsed = 0
            
            # Create a subsurface for the visible portion
            visible_surface = pygame.Surface((max_width, text_surface.get_height()), pygame.SRCALPHA)
            
            if elapsed < self.SCROLL_PAUSE:
                # Initial pause
                x_scroll = 0
            elif elapsed > total_scroll_time - self.SCROLL_PAUSE:
                # Final pause
                x_scroll = text_width - max_width
            else:
                # Scrolling
                scroll_elapsed = elapsed - self.SCROLL_PAUSE
                x_scroll = int(scroll_elapsed * self.SCROLL_SPEED)
                x_scroll = min(x_scroll, text_width - max_width)
            
            # Draw the visible portion of the text
            visible_surface.blit(text_surface, (-x_scroll, 0))
            visible_surface.set_alpha(int(alpha * 255))
            self.screen.blit(visible_surface, ((self.screen_width - max_width) // 2, y_pos))

    def draw_window(self):
        """Draw the window contents."""
        if not pygame.display.get_init():
            return

        # Clear the window
        self.screen.fill((0, 0, 0))  # Black background

        # Get schedule display settings
        schedule_interval = self.config.get('display', {}).get('schedule_interval', 60)  # Default 60 seconds
        schedule_duration = self.config.get('display', {}).get('schedule_duration', 10)  # Default 10 seconds
        current_time = time.time()

        # Check if we should enable permanent schedule display (10 minutes without song change)
        if self.last_song_time and current_time - self.last_song_time >= 600:  # 600 seconds = 10 minutes
            self.permanent_schedule = True
            self.schedule_showing = True
        # Reset permanent schedule when a new song is detected
        elif self.last_song_time and current_time - self.last_song_time < 600:
            self.permanent_schedule = False

        # Check if it's time to show the schedule (only if not permanent)
        if not self.permanent_schedule:
            if not self.schedule_showing and current_time - self.last_schedule_display >= schedule_interval:
                self.schedule_showing = True
                self.schedule_show_start = current_time
                self.last_schedule_display = current_time

            # Check if we should stop showing the schedule
            if self.schedule_showing and current_time - self.schedule_show_start >= schedule_duration:
                self.schedule_showing = False

        # Draw the current background if it exists and we're not showing the schedule
        if self.current_background is not None and not self.schedule_showing:
            # Get the current display size
            display_width, display_height = pygame.display.get_surface().get_size()
            
            # Get the original image dimensions
            img_width = self.current_background.get_width()
            img_height = self.current_background.get_height()
            
            # Calculate the scale to fit the image while maintaining aspect ratio
            scale = min(display_width / img_width, 
                      display_height / img_height)
            
            # Calculate the base dimensions that maintain the aspect ratio
            base_width = int(img_width * scale)
            base_height = int(img_height * scale)
            
            if self.is_stretched:
                # When stretched, only increase the width by 4/3 to compensate for CRT squish
                target_width = int(base_width * (4/3))
                target_height = base_height
            else:
                target_width = base_width
                target_height = base_height
            
            # Create a temporary surface for the final image
            scaled_surface = pygame.transform.smoothscale(
                self.current_background,
                (target_width, target_height)
            )
            
            # Center the image
            x_pos = (display_width - target_width) // 2
            y_pos = (display_height - target_height) // 2
            self.screen.blit(scaled_surface, (x_pos, y_pos))

        # Draw song info if available and we're not showing the schedule
        if self.last_identified and self.last_song_time and not self.schedule_showing:
            current_time = time.time()
            elapsed_time = current_time - self.last_song_time
            
            # Calculate alpha (transparency) value
            if elapsed_time < self.show_duration:
                alpha = 255  # Fully visible
            elif elapsed_time < (self.show_duration + self.fade_duration):
                # Linear fade out over fade_duration seconds
                fade_progress = (elapsed_time - self.show_duration) / self.fade_duration
                alpha = int(255 * (1 - fade_progress))
            else:
                alpha = 0  # Fully transparent
            
            if alpha > 0:  # Only draw if not fully transparent
                # Create larger fonts for title and artist
                title_font = pygame.font.Font(None, 72)  # Larger font for title
                artist_font = pygame.font.Font(None, 48)  # Slightly smaller for artist
                
                # Render text with outline
                title_surface = self.render_text_with_outline(
                    self.last_identified['title'],
                    title_font,
                    (255, 255, 255),  # White text
                    (0, 0, 0),        # Black outline
                    3                  # Outline width
                )
                artist_surface = self.render_text_with_outline(
                    self.last_identified['artist'],
                    artist_font,
                    (255, 255, 255),  # White text
                    (0, 0, 0),        # Black outline
                    2                  # Slightly smaller outline for artist
                )
                
                # Calculate maximum width for text (80% of screen width)
                max_width = int(self.screen_width * 0.8)
                
                # Draw title with scrolling if needed
                self.draw_scrolling_text(
                    title_surface,
                    self.screen_height // 2 - 40,  # Title position
                    alpha,
                    max_width
                )
                
                # Draw artist name (centered, no scroll needed for artist)
                artist_rect = artist_surface.get_rect(center=(self.screen_width // 2, self.screen_height // 2 + 40))
                self.screen.blit(artist_surface, artist_rect)

        # Draw notification on top if active
        self.draw_notification()
        
        pygame.display.flip()

    async def display_album_art(self, track):
        """Display album art on screen with improved error handling and caching."""
        if not track or 'images' not in track:
            self.logger.warning("No album art found in track data")
            self.current_background = None
            return

        try:
            # Get the highest quality image available
            image_url = None
            if 'coverarthq' in track['images']:
                image_url = track['images']['coverarthq']
            elif 'coverart' in track['images']:
                image_url = track['images']['coverart']
            
            if not image_url:
                self.logger.warning("No suitable album art URL found")
                self.current_background = None
                return

            # Implement caching to avoid re-downloading the same image
            cache_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'cache')
            os.makedirs(cache_dir, exist_ok=True)
            
            # Create a cache key from the URL
            cache_key = hashlib.md5(image_url.encode()).hexdigest()
            cache_path = os.path.join(cache_dir, f"{cache_key}.jpg")

            # Check if image is already cached
            if os.path.exists(cache_path):
                self.logger.debug("Loading album art from cache")
                image_data = open(cache_path, 'rb').read()
            else:
                # Download with timeout and retries
                async with aiohttp.ClientSession() as session:
                    async with session.get(image_url, timeout=10) as response:
                        if response.status == 200:
                            image_data = await response.read()
                            # Cache the downloaded image
                            with open(cache_path, 'wb') as f:
                                f.write(image_data)
                        else:
                            raise Exception(f"Failed to download image: {response.status}")

            # Load image with Pygame
            image_stream = BytesIO(image_data)
            image = pygame.image.load(image_stream)
            
            # Convert to RGB mode if necessary (handles PNG transparency)
            if image.get_alpha():
                image = image.convert_alpha()
            else:
                image = image.convert()

            self.current_background = image
            self.logger.debug("Successfully loaded and displayed album art")

        except Exception as e:
            self.logger.error(f"Error displaying album art: {e}")
            if self.debug_mode:
                import traceback
                self.logger.debug(traceback.format_exc())
            self.current_background = None

    async def run(self):
        """Main application loop with improved music detection."""
        try:
            # Initialize Pygame display
            pygame.display.set_caption("Music Identifier")
            if self.is_fullscreen:
                self.screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
            else:
                self.screen = pygame.display.set_mode((self.screen_width, self.screen_height))
            self.font = pygame.font.Font(None, 36)

            # Initialize audio stream with error handling
            try:
                self.stream = self.p.open(
                    format=self.FORMAT,
                    channels=self.CHANNELS,
                    rate=self.RATE,
                    input=True,
                    input_device_index=self.input_device_index,
                    frames_per_buffer=self.CHUNK,
                    start=True  # Start the stream immediately
                )
            except IOError as e:
                self.logger.error(f"Error opening stream: {str(e)}")
                if hasattr(self, 'stream'):
                    self.stream.close()
                raise
            
            # Create a buffer for audio data
            buffer = []
            buffer_duration = 0
            target_duration = 3  # seconds of audio to collect

            while True:
                # Handle Pygame events
                self.handle_events()
                
                if not self._is_within_operating_hours() and not self.always_open:
                    await asyncio.sleep(1)
                    self.draw_window()
                    pygame.display.flip()
                    continue

                # Read audio data
                try:
                    data = self.stream.read(self.CHUNK, exception_on_overflow=False)
                    # Convert data to numpy array (now using int16)
                    audio_chunk = np.frombuffer(data, dtype=np.int16)
                    buffer.append(audio_chunk)
                    buffer_duration += self.CHUNK / self.RATE

                    # Once we have enough audio data
                    if buffer_duration >= target_duration:
                        # Concatenate all chunks
                        audio_array = np.concatenate(buffer)
                        
                        # Create WAV data directly without intermediate conversions
                        import wave
                        import io
                        wav_buffer = io.BytesIO()
                        with wave.open(wav_buffer, 'wb') as wav_file:
                            wav_file.setnchannels(self.CHANNELS)
                            wav_file.setsampwidth(2)  # 16-bit audio
                            wav_file.setframerate(self.RATE)
                            wav_file.writeframes(audio_array.tobytes())
                        
                        audio_data = wav_buffer.getvalue()
                        wav_buffer.close()

                        # Clear the buffer
                        buffer.clear()
                        buffer_duration = 0

                        try:
                            # Recognize song using the WAV data
                            result = await self.shazam.recognize(audio_data)
                            
                            if result and 'track' in result:
                                # Create new song info from the raw result first
                                current_song = {
                                    'title': result['track'].get('title', 'Unknown Title'),
                                    'artist': result['track'].get('subtitle', 'Unknown Artist')
                                }

                                # Check if this is a new song
                                if (not self.last_identified or 
                                    current_song['title'] != self.last_identified['title'] or
                                    current_song['artist'] != self.last_identified['artist']):
                                    
                                    self.last_identified = current_song
                                    self.last_song_time = time.time()
                                    
                                    # Log the identification
                                    self.logger.info(f"Identified: {current_song['title']} by {current_song['artist']}")
                                    
                                    # Display album art
                                    await self.display_album_art(result['track'])
                        except Exception as e:
                            self.logger.error(f"Error in song recognition: {e}")
                            if self.debug_mode:
                                import traceback
                                self.logger.debug(traceback.format_exc())

                except IOError as e:
                    self.logger.error(f"Audio stream error: {e}")
                    await asyncio.sleep(0.1)
                    continue

                # Update display
                self.draw_window()
                pygame.display.flip()

                # Small sleep to prevent high CPU usage
                await asyncio.sleep(0.01)

        except Exception as e:
            self.logger.error(f"Fatal error in run loop: {e}")
            if self.debug_mode:
                import traceback
                self.logger.debug(traceback.format_exc())
        finally:
            if hasattr(self, 'stream'):
                self.stream.stop_stream()
                self.stream.close()
            pygame.quit()

    def start_stream(self):
        """Start the audio stream with robust error handling."""
        try:
            if not hasattr(self, 'stream') or self.stream is None:
                self.logger.error("Audio stream not initialized")
                return False
            
            # Ensure the stream isn't already active
            if self.stream.is_active():
                self.logger.warning("Stream is already active")
                return True
            
            # Start the stream with error checking
            try:
                self.stream.start_stream()
                if not self.stream.is_active():
                    self.logger.error("Failed to start stream - stream not active after start")
                    return False
            except Exception as e:
                self.logger.error(f"Error starting stream: {str(e)}")
                return False
            
            self.logger.info("Audio stream started successfully")
            return True
            
        except Exception as e:
            self.logger.error(f"Unexpected error in start_stream: {str(e)}")
            return False

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
                # Draw semi-transparent background
                notification_surface = pygame.Surface((self.screen_width, 80))
                notification_surface.set_alpha(200)
                notification_surface.fill((0, 0, 0))
                self.screen.blit(notification_surface, (0, 0))

                # Draw title and message
                title_font = pygame.font.Font(None, 36)
                message_font = pygame.font.Font(None, 24)

                title_text = title_font.render(self.notification['title'], True, (255, 255, 255))
                message_text = message_font.render(self.notification['message'], True, (200, 200, 200))

                title_rect = title_text.get_rect(centerx=self.screen_width//2, top=10)
                message_rect = message_text.get_rect(centerx=self.screen_width//2, top=45)

                self.screen.blit(title_text, title_rect)
                self.screen.blit(message_text, message_rect)
            else:
                delattr(self, 'notification')

    def _update_screensaver(self, text, font_size=36):
        """Update and render the screensaver text with wrapping and bouncing movement."""
        current_time = time.time()
        dt = current_time - self.screensaver_last_update
        self.screensaver_last_update = current_time

        # Update position
        self.screensaver_pos[0] += self.screensaver_velocity[0]
        self.screensaver_pos[1] += self.screensaver_velocity[1]

        # Update color (smooth color cycling)
        for i in range(3):
            color_val = self.screensaver_color[i] + self.screensaver_color_direction[i]
            if color_val >= 255 or color_val <= 100:  # Keep colors bright enough
                self.screensaver_color_direction[i] *= -1
                color_val = max(100, min(255, color_val))
            self.screensaver_color = tuple(
                self.screensaver_color[j] + self.screensaver_color_direction[j]
                if j == i else self.screensaver_color[j]
                for j in range(3)
            )

        # Create font
        font = pygame.font.Font(None, font_size)
        
        # Word wrap the text
        words = text.split()
        lines = []
        current_line = []
        max_width = self.screen_width * 0.8  # Use 80% of screen width

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

        # Render all lines
        text_surfaces = [font.render(line, True, self.screensaver_color) for line in lines]
        total_height = sum(surface.get_height() for surface in text_surfaces)
        max_text_width = max(surface.get_width() for surface in text_surfaces)

        # Create a surface containing all lines
        text_surface = pygame.Surface((max_text_width, total_height), pygame.SRCALPHA)
        current_y = 0
        for surface in text_surfaces:
            text_surface.blit(surface, ((max_text_width - surface.get_width()) // 2, current_y))
            current_y += surface.get_height()

        # Check bounds and bounce
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
            # Convert GitHub Gist URL to raw URL
            if 'gist.github.com' in base_url:
                # Extract the Gist ID and construct raw URL
                gist_id = base_url.split('/')[-1]
                url = f"https://gist.githubusercontent.com/wjhrdy/{gist_id}/raw"
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
        # Initial delay to let the app start up
        await asyncio.sleep(2)
        
        while True:
            try:
                self.logger.debug("Checking for remote config updates...")
                remote_config = await self._fetch_remote_config()
                if remote_config:
                    self.logger.debug(f"Received remote config: {remote_config}")
                    
                    with self.config_lock:
                        # Deep compare the configs
                        if yaml.dump(remote_config, sort_keys=True) != yaml.dump(self.config, sort_keys=True):
                            self.logger.debug("Remote config differs from local config, updating...")
                            # Save updated config to file first
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
                
                # Get update interval from config, default to 1 hour
                update_interval = self.config.get('remote', {}).get('update_interval', 3600)
                self.logger.debug(f"Next config check in {update_interval} seconds")
                await asyncio.sleep(update_interval)
            except Exception as e:
                self.logger.error(f"Error in config update loop: {e}")
                if self.debug_mode:
                    import traceback
                    self.logger.debug(f"Full traceback: {traceback.format_exc()}")
                await asyncio.sleep(60)  # Wait a minute before retrying on error

    def toggle_fullscreen(self):
        """Toggle between fullscreen and windowed mode."""
        self.is_fullscreen = not self.is_fullscreen
        if self.is_fullscreen:
            self.screen = pygame.display.set_mode((0, 0), pygame.FULLSCREEN)
        else:
            # Return to windowed mode with original dimensions
            self.screen = pygame.display.set_mode((self.screen_width, self.screen_height))

    def toggle_stretch_mode(self):
        """Toggle between normal and stretched mode to compensate for 16:9 to 4:3 conversion."""
        self.is_stretched = not self.is_stretched

    def handle_events(self):
        """Handle pygame events including fullscreen and stretch toggles."""
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                if hasattr(self, 'p') and self.p:
                    self.p.terminate()
                if hasattr(self, 'config_update_task') and self.config_update_task:
                    self.config_update_task.cancel()
                pygame.quit()
                sys.exit(0)
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_f:
                    self.toggle_fullscreen()
                elif event.key == pygame.K_s:
                    self.toggle_stretch_mode()
                elif event.key == pygame.K_ESCAPE and self.is_fullscreen:
                    self.toggle_fullscreen()
        return True

    def _get_schedule_message(self):
        """Get a formatted message about the schedule and current status."""
        if not self.config or 'schedule' not in self.config:
            return "AL is running 24/7"

        # Get display settings with defaults
        display_config = self.config.get('display', {})
        header = display_config.get('schedule_header', 'Operating Hours')
        time_format = display_config.get('schedule_time_format', '{open} - {close}')
        
        # Format all scheduled days
        schedule_text = f"{header}:\n"
        
        # Group days with same hours
        hours_to_days = {}
        day_order = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
        day_indices = {day: i for i, day in enumerate(day_order)}
        
        for item in self.config['schedule']:
            hours = time_format.format(open=item['open'], close=item['close'])
            if hours not in hours_to_days:
                hours_to_days[hours] = []
            hours_to_days[hours].append(item['day'])
        
        # Process each group of hours
        for hours, days in hours_to_days.items():
            # Sort days according to day_order
            days.sort(key=lambda x: day_indices[x])
            
            # Find consecutive day ranges
            ranges = []
            range_start = days[0]
            prev_idx = day_indices[days[0]]
            
            for day in days[1:]:
                curr_idx = day_indices[day]
                if curr_idx != prev_idx + 1:
                    # End of a range
                    if range_start == days[days.index(day)-1]:
                        ranges.append(range_start)
                    else:
                        ranges.append(f"{range_start}-{days[days.index(day)-1]}")
                    range_start = day
                prev_idx = curr_idx
            
            # Add the last range
            if range_start == days[-1]:
                ranges.append(range_start)
            else:
                ranges.append(f"{range_start}-{days[-1]}")
            
            # Add to schedule text with days and hours on separate lines
            schedule_text += f"{', '.join(ranges)}\n{hours}\n"

        return schedule_text

async def main():
    import argparse
    parser = argparse.ArgumentParser(description='Music Recognition App')
    parser.add_argument('--debug', action='store_true', help='Enable debug mode')
    parser.add_argument('--list-devices', action='store_true', help='List available audio devices and exit')
    parser.add_argument('--device', type=int, help='Select input device by number')
    parser.add_argument('--fullscreen', action='store_true', help='Start in fullscreen mode')
    parser.add_argument('--always-open', action='store_true', help='Always stay open')
    
    try:
        args = parser.parse_args()
    except SystemExit as e:
        raise e
    except Exception as e:
        print(f"Warning: Error parsing arguments ({str(e)}), using defaults")
        class Args:
            debug = False
            list_devices = False
            device = None
            fullscreen = False
            always_open = False
        args = Args()
    
    if args.list_devices:
        MusicIdentifier.list_devices()
        sys.exit(0)
    
    # Check for AL_DEVICE environment variable
    env_device = os.environ.get('AL_DEVICE')
    if env_device is not None:
        try:
            device_index = int(env_device)
            if args.device is not None:
                print(f"Warning: Both --device argument ({args.device}) and AL_DEVICE environment variable ({device_index}) are set.")
                print(f"Using AL_DEVICE value: {device_index}")
            args.device = device_index
        except ValueError:
            print(f"Warning: Invalid AL_DEVICE value: {env_device}. Must be an integer.")
    
    app = MusicIdentifier(debug_mode=args.debug, device_index=args.device, always_open=args.always_open)
    if args.fullscreen:
        app.is_fullscreen = True
    await app.run()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nShutting down gracefully...")
    finally:
        # Ensure pygame is quit even if other cleanup fails
        pygame.quit()
        print("Cleanup complete")