import requests
import m3u8
import os
import sys
from urllib.parse import urljoin, urlparse, urlunparse, parse_qs, urlencode
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm

# Suppress InsecureRequestWarning for unverified HTTPS requests if needed
# from requests.packages.urllib3.exceptions import InsecureRequestWarning
# requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

def download_segment(session, segment_url, output_dir, segment_filename, headers, verify_ssl=True):
    """Downloads a single video segment using the provided session."""
    filepath = os.path.join(output_dir, segment_filename)
    try:
        response = session.get(segment_url, headers=headers, stream=True, timeout=20, verify=verify_ssl)
        response.raise_for_status()  # Raise an exception for bad status codes
        with open(filepath, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        return filepath
    except requests.exceptions.RequestException as e:
        print(f"\nError downloading segment {segment_url}: {e}")
        return None
    except Exception as e:
        print(f"\nError writing segment {segment_filename}: {e}")
        return None

def main(m3u8_url, output_filename):
    """Downloads all segments from an M3U8 playlist and combines them."""
    temp_dir = "temp_segments"
    os.makedirs(temp_dir, exist_ok=True)

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Referer': m3u8_url # Add Referer header using the initial M3U8 URL
    }
    # Set verify_ssl to False if you encounter SSL certificate errors, but be aware of the security implications.
    verify_ssl = True 

    session = requests.Session()
    session.headers.update(headers) # Apply headers to the session

    print(f"Fetching M3U8 playlist from: {m3u8_url}")
    try:
        # Parse original URL to potentially extract query parameters
        original_parsed_url = urlparse(m3u8_url)
        original_query_params = parse_qs(original_parsed_url.query)

        playlist_response = session.get(m3u8_url, timeout=15, verify=verify_ssl)
        playlist_response.raise_for_status()
        playlist_content = playlist_response.text
        playlist = m3u8.loads(playlist_content, uri=m3u8_url)

        # Check if it's a master playlist (variant)
        if playlist.is_variant:
            print("Master playlist detected. Available streams:")
            selected_playlist = None
            target_resolution = "240p" # Attempt to find based on user's last URL, can be made more robust
            
            for p in playlist.playlists:
                stream_info = p.stream_info
                resolution = f"{stream_info.resolution[1]}p" if stream_info.resolution else "Unknown resolution"
                bandwidth = stream_info.bandwidth / 1000 if stream_info.bandwidth else "Unknown bandwidth"
                print(f"- Resolution: {resolution}, Bandwidth: {bandwidth} kbps, URL: {p.absolute_uri}")
                
                # Try to select the target resolution, fallback to first playlist
                if target_resolution in p.uri and not selected_playlist:
                     selected_playlist = p
                elif not selected_playlist: # Fallback to the first one if target not found yet
                     selected_playlist = p

            if not selected_playlist:
                print("Could not find a suitable media playlist in the master playlist.")
                sys.exit(1)

            print(f"\nSelected stream: {selected_playlist.absolute_uri}")
            # Fetch and parse the selected media playlist
            try:
                # Construct the media playlist URL, preserving original query params
                media_parsed_url = urlparse(selected_playlist.absolute_uri)
                media_query_params = parse_qs(media_parsed_url.query)
                # Merge original params, giving precedence to media-specific ones if conflicts exist
                merged_query_params = {**original_query_params, **media_query_params}
                
                # Rebuild URL
                final_media_url_parts = list(media_parsed_url)
                final_media_url_parts[4] = urlencode(merged_query_params, doseq=True) # Update query string
                final_media_url = urlunparse(final_media_url_parts)

                print(f"Fetching selected media playlist with combined params: {final_media_url}")
                playlist_response = session.get(final_media_url, timeout=15, verify=verify_ssl)
                playlist_response.raise_for_status()
                # Use the final_media_url as the base URI for parsing segments later
                playlist = m3u8.loads(playlist_response.text, uri=final_media_url)
            except requests.exceptions.RequestException as e:
                print(f"Error fetching selected media playlist: {e}")
                sys.exit(1)
            except Exception as e:
                print(f"Error parsing selected media playlist: {e}")
                sys.exit(1)

    except requests.exceptions.RequestException as e:
        print(f"Error fetching initial playlist: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"Error parsing playlist: {e}")
        sys.exit(1)

    # Now playlist should be a media playlist
    if not playlist.segments:
        print("No video segments found in the final M3U8 playlist (master or media).")
        sys.exit(1)

    segment_urls = [urljoin(playlist.base_uri, segment.uri) for segment in playlist.segments]
    total_segments = len(segment_urls)
    print(f"Found {total_segments} segments.")

    downloaded_files = []
    max_workers = 10 # Adjust based on your network and system capacity

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(download_segment, session, url, temp_dir, f"segment_{i:05d}.ts", headers, verify_ssl): i
            for i, url in enumerate(segment_urls)
        }
        
        try:
            for future in tqdm(as_completed(futures), total=total_segments, desc="Downloading Segments", unit="seg"):
                result = future.result()
                if result:
                    downloaded_files.append(result)
                else:
                    # Handle failed download if necessary (e.g., retry or abort)
                    print(f"\nSegment download failed, skipping.") 
        except KeyboardInterrupt:
            print("\nDownload interrupted by user. Cleaning up...")
            executor.shutdown(wait=False, cancel_futures=True)
            # Clean up partially downloaded files
            for f in downloaded_files:
                if os.path.exists(f): os.remove(f)
            if os.path.exists(temp_dir): os.rmdir(temp_dir) # Remove temp dir only if empty
            sys.exit(1)


    if len(downloaded_files) != total_segments:
         print(f"\nWarning: Only {len(downloaded_files)} out of {total_segments} segments were downloaded successfully.")
         # Decide if you want to proceed with combining partial files or exit
         # For now, we'll proceed but warn the user.

    # Ensure files are sorted correctly based on the index 'i' used in filename
    downloaded_files.sort() 

    print("\nCombining segments...")
    # Create a file list for ffmpeg concatenation
    concat_list_path = os.path.join(temp_dir, "concat_list.txt")
    with open(concat_list_path, 'w', encoding='utf-8') as f:
        for segment_file in downloaded_files:
            # Need to escape special characters and handle paths correctly for ffmpeg's concat demuxer
            # Simple relative paths should be okay if ffmpeg runs in the same CWD or paths are absolute
            # For robustness, especially with complex filenames, consider absolute paths or careful escaping.
            # Using forward slashes generally works better across platforms for ffmpeg.
            # Use absolute paths and replace backslashes for ffmpeg compatibility
            absolute_path = os.path.abspath(segment_file).replace('\\', '/')
            f.write(f"file '{absolute_path}'\n")


    # Use ffmpeg to concatenate the downloaded segments
    # The -safe 0 option is needed if using relative paths outside the CWD or absolute paths.
    ffmpeg_command = f'ffmpeg -f concat -safe 0 -i "{concat_list_path}" -c copy "{output_filename}"'
    
    print(f"Executing: {ffmpeg_command}")
    try:
        # Using os.system might be simpler here, but subprocess offers more control
        import subprocess
        process = subprocess.run(ffmpeg_command, shell=True, check=True, capture_output=True, text=True)
        print("FFmpeg Output:\n", process.stdout)
        if process.stderr:
             print("FFmpeg Errors:\n", process.stderr)
        print(f"\nVideo successfully combined into {output_filename}")
    except subprocess.CalledProcessError as e:
        print(f"\nError during ffmpeg concatenation: {e}")
        print("FFmpeg Output:\n", e.stdout)
        print("FFmpeg Errors:\n", e.stderr)
    except FileNotFoundError:
        print("\nError: ffmpeg command not found. Make sure ffmpeg is installed and in your system's PATH.")
    except Exception as e:
        print(f"\nAn unexpected error occurred during concatenation: {e}")


    # Clean up temporary files
    print("Cleaning up temporary files...")
    try:
        for segment_file in downloaded_files:
             if os.path.exists(segment_file):
                 os.remove(segment_file)
        if os.path.exists(concat_list_path):
             os.remove(concat_list_path)
        os.rmdir(temp_dir)
        print("Cleanup complete.")
    except OSError as e:
        print(f"Error during cleanup: {e}. Manual cleanup of '{temp_dir}' might be required.")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python m3u8_downloader.py <m3u8_url> <output_filename.mp4>")
        sys.exit(1)
    
    m3u8_url_arg = sys.argv[1]
    output_filename_arg = sys.argv[2]
    
    main(m3u8_url_arg, output_filename_arg)