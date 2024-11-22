import pyaudio
import numpy as np
from shazamio import Shazam, Serialize
import asyncio
import pygame
import requests
from io import BytesIO
import time
import warnings
import sys
import logging
from pydub import AudioSegment
import threading
from queue import Queue, Empty

# Suppress urllib3 warnings
warnings.filterwarnings('ignore', category=Warning)

class MusicIdentifier:
    def __init__(self, debug_mode=False):
        self.debug_mode = debug_mode
        self.start_time = time.time()
        
        # Set up logging
        log_level = logging.DEBUG if debug_mode else logging.INFO
        logging.basicConfig(
            level=log_level,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )
        self.logger = logging.getLogger(__name__)
        self.logger.debug("Initializing MusicIdentifier in debug mode" if debug_mode else "Initializing MusicIdentifier")
        
        # Create debug output directory
        if debug_mode:
            import os
            self.debug_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'debug_output')
            os.makedirs(self.debug_dir, exist_ok=True)
        
        # Initialize Shazam
        self.shazam = Shazam()
        
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
        
        # Audio parameters
        self.FORMAT = pyaudio.paFloat32
        self.CHANNELS = 1
        self.RATE = 44100
        self.CHUNK = 1024  # Smaller chunk size for smoother recording
        self.RECORD_SECONDS = 5
        
        # Audio recording state
        self.recording = False
        self.frames = []
        self.audio_queue = Queue()
        self.result_queue = Queue()
        self.recording_thread = None
        self.processing_thread = None
        
        # Initialize PyAudio
        self.p = pyaudio.PyAudio()
        self.input_device_index = self._find_input_device()
        
        self.logger.debug("Initialization complete")
    
    def _find_input_device(self):
        """Find and return the first available input device index"""
        for i in range(self.p.get_device_count()):
            device_info = self.p.get_device_info_by_index(i)
            if self.debug_mode:
                self.logger.info(f"\nDevice {i}: {device_info['name']}")
                self.logger.info(f"  Max Input Channels: {device_info['maxInputChannels']}")
            if device_info.get('maxInputChannels') > 0:
                return i
        self.logger.error("No input devices found!")
        sys.exit(1)

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
            # If text fits, center it precisely using float division and rounding
            x_pos = round((max_width - text_width) / 2)
            # Create a temporary surface to handle alpha
            temp_surface = pygame.Surface((text_width, text_surface.get_height()), pygame.SRCALPHA)
            temp_surface.blit(text_surface, (0, 0))
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

        # Draw the current background if it exists
        if self.current_background is not None:
            # Get the current display size
            display_width, display_height = pygame.display.get_surface().get_size()
            
            # Get the original image dimensions
            img_width = self.current_background.get_width()
            img_height = self.current_background.get_height()
            
            # Calculate the scale to fit the image while maintaining aspect ratio
            scale = min(display_width / img_width, display_height / img_height)
            
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

        # Draw song info if available and calculate alpha based on time
        if self.last_identified and self.last_song_time:
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
                
                # Create transparent surfaces for fade effect
                title_alpha = pygame.Surface(title_surface.get_size(), pygame.SRCALPHA)
                artist_alpha = pygame.Surface(artist_surface.get_size(), pygame.SRCALPHA)
                
                # Fill with transparent color
                title_alpha.fill((255, 255, 255, alpha))
                artist_alpha.fill((255, 255, 255, alpha))
                
                # Blit using alpha as a mask
                title_surface.blit(title_alpha, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
                artist_surface.blit(artist_alpha, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
                
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

        pygame.display.flip()

    def display_album_art(self, track):
        """Display album art on screen."""
        try:
            artwork_url = track.get('images', {}).get('coverart')
            if not artwork_url:
                if self.debug_mode:
                    self.logger.debug("No album art URL found")
                return False
            
            # Download and display the album art
            response = requests.get(artwork_url)
            image = pygame.image.load(BytesIO(response.content))
            
            # Scale and center image
            scale = min(self.screen_width / image.get_width(), 
                      self.screen_height / image.get_height())
            new_size = (int(image.get_width() * scale), 
                      int(image.get_height() * scale))
            image = pygame.transform.scale(image, new_size)
            
            # Create a new surface for the background
            self.current_background = pygame.Surface((self.screen_width, self.screen_height))
            self.current_background.fill((0, 0, 0))
            
            # Center the image
            image_x = (self.screen_width - new_size[0]) // 2
            image_y = (self.screen_height - new_size[1]) // 2
            self.current_background.blit(image, (image_x, image_y))
            
            # Copy background to screen
            self.screen.blit(self.current_background, (0, 0))
            pygame.display.flip()
            return True
            
        except Exception as e:
            self.logger.error(f"Error displaying album art: {e}")
            if self.debug_mode:
                self.logger.debug(f"Track data: {track}")
            return False

    def process_audio_and_recognize(self, audio_data):
        """Process audio and recognize song in a separate thread."""
        try:
            # Convert audio to int16
            audio_array = np.frombuffer(b''.join(audio_data), dtype=np.float32)
            audio_int16 = (audio_array * 32767).astype(np.int16)
            
            # Create AudioSegment
            audio_segment = AudioSegment(
                audio_int16.tobytes(), 
                frame_rate=self.RATE,
                sample_width=2,  # 16-bit audio
                channels=self.CHANNELS
            )
            
            if self.debug_mode:
                self.last_audio_segment = audio_segment
            
            # Export to WAV format in memory
            buffer = audio_segment.export(format="wav")
            audio_bytes = buffer.read()

            # Run song recognition
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                out = loop.run_until_complete(self.shazam.recognize(data=audio_bytes))
                
                if not out or not out.get('track'):
                    if self.debug_mode:
                        self.logger.debug("No song detected")
                    self.result_queue.put(None)
                    return

                track = out['track']
                
                # Create new song info
                new_song = {
                    'title': track.get('title', 'Unknown Title'),
                    'artist': track.get('subtitle', 'Unknown Artist'),
                    'artwork_url': track.get('images', {}).get('coverart', None)
                }
                
                # Put the result in the queue
                self.result_queue.put((new_song, track, audio_segment if self.debug_mode else None))
                
            finally:
                loop.close()
                
        except Exception as e:
            self.logger.error(f"Error processing audio: {e}")
            if self.debug_mode:
                import traceback
                self.logger.debug(f"Full traceback: {traceback.format_exc()}")
            self.result_queue.put(None)

    def record_audio(self):
        """Record audio in a separate thread."""
        stream = self.p.open(
            format=self.FORMAT,
            channels=self.CHANNELS,
            rate=self.RATE,
            input=True,
            input_device_index=self.input_device_index,
            frames_per_buffer=self.CHUNK
        )
        
        self.frames = []
        start_time = time.time()
        
        while time.time() - start_time < self.RECORD_SECONDS and self.recording:
            try:
                data = stream.read(self.CHUNK, exception_on_overflow=False)
                self.frames.append(data)
                time.sleep(0.001)  # Small sleep to prevent thread from hogging CPU
            except Exception as e:
                self.logger.error(f"Error recording audio: {e}")
                break
        
        stream.stop_stream()
        stream.close()
        
        # Start processing in a new thread
        self.processing_thread = threading.Thread(
            target=self.process_audio_and_recognize,
            args=(self.frames.copy(),)
        )
        self.processing_thread.start()
        
        self.recording = False

    async def check_recognition_result(self):
        """Check for song recognition results."""
        try:
            result = self.result_queue.get_nowait()
            if result:
                new_song, track, audio_segment = result
                
                # Check if it's a different song
                is_new_song = (not isinstance(self.last_identified, dict) or
                             self.last_identified.get('title') != new_song['title'] or
                             self.last_identified.get('artist') != new_song['artist'])
                
                if is_new_song:
                    # Store the new song info and update timestamp
                    self.last_identified = new_song
                    self.last_song_time = time.time()
                    
                    if self.debug_mode:
                        self.logger.info(f"New song identified: {self.last_identified['title']} by {self.last_identified['artist']}")
                        # Save debug audio file
                        import os
                        import re
                        
                        # Clean filename of invalid characters
                        def clean_filename(s):
                            return re.sub(r'[<>:"/\\|?*]', '_', s)
                        
                        filename_base = clean_filename(f"{self.last_identified['title']}_by_{self.last_identified['artist']}")
                        timestamp = time.strftime("%Y%m%d_%H%M%S")
                        
                        # Save audio
                        audio_path = os.path.join(self.debug_dir, f"{filename_base}_{timestamp}.wav")
                        audio_segment.export(audio_path, format="wav")
                        self.logger.debug(f"Saved audio to: {audio_path}")
                    
                    # Display album art if available
                    self.display_album_art(track)
            
            if self.processing_thread and not self.processing_thread.is_alive():
                self.processing_thread = None
                
        except Empty:
            pass

    async def start_recording(self):
        """Start recording audio in a separate thread."""
        if self.recording:
            return
        
        self.recording = True
        self.recording_thread = threading.Thread(target=self.record_audio)
        self.recording_thread.start()

    async def run(self):
        """Main application loop."""
        try:
            pygame.display.set_caption("Music Recognition")
            self.screen = pygame.display.set_mode((self.screen_width, self.screen_height))
            self.font = pygame.font.Font(None, 36)
            
            running = True
            last_record_time = time.time()
            
            while running:
                current_time = time.time()
                
                # Handle events and update UI
                running = self.handle_events()
                if not running:
                    break
                
                self.draw_window()
                
                # Start recording if it's time and we're not already recording
                if not self.recording and not self.processing_thread and current_time - last_record_time >= self.RECORD_SECONDS:
                    await self.start_recording()
                    last_record_time = current_time
                
                # Check for recognition results
                await self.check_recognition_result()
                
                # Small sleep to prevent high CPU usage
                await asyncio.sleep(0.016)  # Approximately 60 FPS
                
        except Exception as e:
            self.logger.error(f"Error in main loop: {e}")
            if self.debug_mode:
                import traceback
                self.logger.debug(f"Full traceback: {traceback.format_exc()}")
        
        finally:
            pygame.quit()

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
                return False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_f:
                    self.toggle_fullscreen()
                elif event.key == pygame.K_s:
                    self.toggle_stretch_mode()
                elif event.key == pygame.K_ESCAPE and self.is_fullscreen:
                    self.toggle_fullscreen()
        return True

async def main():
    import argparse
    parser = argparse.ArgumentParser(description='Music Recognition App')
    parser.add_argument('--debug', action='store_true', help='Enable debug mode')
    args = parser.parse_args()
    
    app = MusicIdentifier(debug_mode=args.debug)
    await app.run()

if __name__ == "__main__":
    asyncio.run(main())