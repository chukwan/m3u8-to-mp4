import requests
import m3u8
import os
import sys
import subprocess
import shutil # For safer directory removal
from urllib.parse import urljoin, urlparse, urlunparse, parse_qs, urlencode
from concurrent.futures import ThreadPoolExecutor, as_completed

# --- Custom Exception ---
class DownloaderError(Exception):
    """Custom exception for downloader errors."""
    pass

# --- Helper Functions ---

def _download_segment(session, segment_url, output_dir, segment_filename, headers, verify_ssl=True):
    """Downloads a single video segment using the provided session."""
    filepath = os.path.join(output_dir, segment_filename)
    try:
        response = session.get(segment_url, headers=headers, stream=True, timeout=20, verify=verify_ssl)
        response.raise_for_status()
        with open(filepath, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        return filepath
    except requests.exceptions.RequestException as e:
        print(f"Error downloading segment {segment_url}: {e}") # Log error
        return None # Indicate failure for this segment
    except Exception as e:
        print(f"Error writing segment {segment_filename}: {e}") # Log error
        return None # Indicate failure

def _cleanup_temp_files(temp_dir, downloaded_files, concat_list_path):
    """Cleans up temporary segment files and the concat list."""
    print(f"Cleaning up temporary files in {temp_dir}...")
    try:
        for segment_file in downloaded_files:
             if segment_file and os.path.exists(segment_file): # Check if path is not None
                 os.remove(segment_file)
        if os.path.exists(concat_list_path):
             os.remove(concat_list_path)
        # Only remove temp_dir if it exists and is empty (safer)
        if os.path.exists(temp_dir) and not os.listdir(temp_dir):
             os.rmdir(temp_dir)
        elif os.path.exists(temp_dir):
             print(f"Warning: Temp directory '{temp_dir}' not empty after cleanup attempt.")
        print("Cleanup complete.")
    except OSError as e:
        print(f"Error during cleanup: {e}. Manual cleanup of '{temp_dir}' might be required.")
        # Don't raise an exception here, just log the cleanup issue

# --- Main Download Function ---

def download_m3u8_video(m3u8_url, output_filepath):
    """
    Downloads all segments from an M3U8 playlist and combines them into a single file.

    Args:
        m3u8_url (str): The URL of the M3U8 playlist (master or media).
        output_filepath (str): The full path where the final MP4 file should be saved.

    Raises:
        DownloaderError: If any critical step fails (fetching, parsing, combining).
        FileNotFoundError: If ffmpeg is not found.
    """
    temp_dir = "temp_segments_" + os.path.basename(output_filepath).replace('.', '_')
    os.makedirs(temp_dir, exist_ok=True)
    downloaded_files = [] # Keep track of successfully downloaded segment file paths
    concat_list_path = os.path.join(temp_dir, "concat_list.txt")

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Referer': m3u8_url
    }
    verify_ssl = True
    session = requests.Session()
    session.headers.update(headers)

    try:
        print(f"Fetching M3U8 playlist from: {m3u8_url}")
        original_parsed_url = urlparse(m3u8_url)
        original_query_params = parse_qs(original_parsed_url.query)

        playlist_response = session.get(m3u8_url, timeout=15, verify=verify_ssl)
        playlist_response.raise_for_status()
        playlist_content = playlist_response.text
        playlist = m3u8.loads(playlist_content, uri=m3u8_url)

        # Handle master playlist
        if playlist.is_variant:
            print("Master playlist detected. Selecting best stream (or first).") # Simplified selection
            selected_playlist = playlist.playlists[0] # Default to first/lowest quality
            # TODO: Add logic to select based on desired quality if needed
            # For now, just take the first one listed
            
            if not selected_playlist:
                 raise DownloaderError("Could not find any media playlist in the master playlist.")

            print(f"Selected stream URI: {selected_playlist.uri}")
            
            # Construct the media playlist URL, preserving original query params
            media_parsed_url = urlparse(selected_playlist.absolute_uri)
            media_query_params = parse_qs(media_parsed_url.query)
            merged_query_params = {**original_query_params, **media_query_params}
            final_media_url_parts = list(media_parsed_url)
            final_media_url_parts[4] = urlencode(merged_query_params, doseq=True)
            final_media_url = urlunparse(final_media_url_parts)

            print(f"Fetching selected media playlist: {final_media_url}")
            playlist_response = session.get(final_media_url, timeout=15, verify=verify_ssl)
            playlist_response.raise_for_status()
            playlist = m3u8.loads(playlist_response.text, uri=final_media_url) # Update playlist object

        # Check for segments in the (now guaranteed) media playlist
        if not playlist.segments:
            raise DownloaderError("No video segments found in the final M3U8 playlist.")

        segment_urls = [urljoin(playlist.base_uri, segment.uri) for segment in playlist.segments]
        total_segments = len(segment_urls)
        print(f"Found {total_segments} segments.")

        # Download segments
        max_workers = 10
        failed_segments = 0
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(_download_segment, session, url, temp_dir, f"segment_{i:05d}.ts", headers, verify_ssl): i
                for i, url in enumerate(segment_urls)
            }
            print(f"Downloading {total_segments} segments...")
            # Basic progress indication without tqdm
            completed_count = 0
            for future in as_completed(futures):
                result = future.result()
                completed_count += 1
                if result:
                    downloaded_files.append(result)
                else:
                    failed_segments += 1
                print(f"Progress: {completed_count}/{total_segments} segments processed ({failed_segments} failed).", end='\r')
        print("\nSegment download phase complete.") # Newline after progress indicator

        if failed_segments > 0:
             print(f"Warning: {failed_segments} out of {total_segments} segments failed to download.")
             # Decide whether to proceed or fail based on threshold? For now, proceed if any downloaded.
        
        if not downloaded_files:
             raise DownloaderError("No segments were downloaded successfully.")

        # Ensure files are sorted correctly
        downloaded_files.sort()

        # Create concat list
        print("Creating ffmpeg concat list...")
        with open(concat_list_path, 'w', encoding='utf-8') as f:
            for segment_file in downloaded_files:
                absolute_path = os.path.abspath(segment_file).replace('\\', '/')
                f.write(f"file '{absolute_path}'\n")

        # Combine using ffmpeg
        print("Combining segments with ffmpeg...")
        # Ensure output directory exists
        os.makedirs(os.path.dirname(output_filepath), exist_ok=True) 
        ffmpeg_command = f'ffmpeg -y -f concat -safe 0 -i "{concat_list_path}" -c copy "{output_filepath}"' # Added -y to overwrite
        print(f"Executing: {ffmpeg_command}")
        
        # Specify encoding and error handling for ffmpeg output
        process = subprocess.run(ffmpeg_command, shell=True, check=False, capture_output=True, text=True, encoding='utf-8', errors='ignore')
        
        if process.returncode != 0:
             print("FFmpeg Output:\n", process.stdout)
             print("FFmpeg Errors:\n", process.stderr)
             raise DownloaderError(f"ffmpeg concatenation failed with exit code {process.returncode}. See logs for details.")
        else:
             print("FFmpeg Output:\n", process.stdout) # Log success output too
             if process.stderr: print("FFmpeg Errors/Warnings:\n", process.stderr)
             print(f"Video successfully combined into {output_filepath}")
             return True # Indicate success

    except requests.exceptions.RequestException as e:
        raise DownloaderError(f"Network error fetching playlist: {e}") from e
    except m3u8.errors.ParseError as e:
        raise DownloaderError(f"Error parsing M3U8 playlist: {e}") from e
    except subprocess.CalledProcessError as e: # Should be caught by returncode check now
        raise DownloaderError(f"Error during ffmpeg concatenation: {e}\nOutput:\n{e.stdout}\nErrors:\n{e.stderr}") from e
    except FileNotFoundError as e:
         # Specific check for ffmpeg missing
         if 'ffmpeg' in str(e):
              raise FileNotFoundError("ffmpeg command not found. Make sure ffmpeg is installed and in your system's PATH.") from e
         else:
              raise # Re-raise other FileNotFoundError
    except Exception as e:
        # Catch-all for other unexpected errors during the process
        raise DownloaderError(f"An unexpected error occurred: {e}") from e
    finally:
        # Ensure cleanup happens even if errors occurred
        _cleanup_temp_files(temp_dir, downloaded_files, concat_list_path)

# Note: The if __name__ == "__main__": block is removed as this is now a library.