import os
import threading
import time
import json # Import json for parsing batch data
from flask import Flask, request, render_template, send_from_directory, flash, redirect, url_for
from m3u8_downloader_lib import download_m3u8_video, DownloaderError

# --- Configuration ---
DOWNLOAD_FOLDER = 'downloads'
ALLOWED_EXTENSIONS = {'mp4'} # Currently only used for serving, not upload

app = Flask(__name__)
app.secret_key = 'super secret key' # Change this in a real app!
app.config['DOWNLOAD_FOLDER'] = os.path.abspath(DOWNLOAD_FOLDER)

# Ensure download folder exists
os.makedirs(app.config['DOWNLOAD_FOLDER'], exist_ok=True)

# --- Routes ---

@app.route('/', methods=['GET'])
def index():
    """Renders the main page with the input form."""
    return render_template('index.html')

def run_download_thread(m3u8_url, output_path):
    """Wrapper function to run download in a thread and log errors."""
    try:
        print(f"[Thread Start] Downloading {m3u8_url} to {output_path}")
        success = download_m3u8_video(m3u8_url, output_path)
        if success:
            print(f"[Thread Success] Finished downloading {m3u8_url}")
        else:
            print(f"[Thread Failure] Download reported failure for {m3u8_url}")
    except Exception as e:
        # Log exceptions occurring within the thread
        print(f"[Thread Error] Failed to download {m3u8_url}: {e}")
        # Optionally log traceback here too
        # import traceback
        # traceback.print_exc()

@app.route('/download', methods=['POST'])
def handle_download():
    """Handles the batch download request from the form."""
    batch_data_json = request.form.get('batch_data')
    if not batch_data_json:
        flash('No batch data received.', 'error')
        return redirect(url_for('index'))

    try:
        batch_items = json.loads(batch_data_json)
    except json.JSONDecodeError:
        flash('Invalid batch data format.', 'error')
        return redirect(url_for('index'))

    if not isinstance(batch_items, list) or not batch_items:
        flash('Batch data is empty or not a list.', 'error')
        return redirect(url_for('index'))

    started_count = 0
    skipped_count = 0

    for item in batch_items:
        if not isinstance(item, dict) or 'url' not in item:
            print(f"Skipping invalid batch item: {item}")
            skipped_count += 1
            continue

        m3u8_url = item['url'].strip()
        desired_filename = item.get('filename', '').strip()

        # --- Validation per item ---
        if not m3u8_url:
            print(f"Skipping item with empty URL.")
            skipped_count += 1
            continue

        if not m3u8_url.startswith(('http://', 'https://')) or not m3u8_url.endswith('.m3u8'):
             print(f"Skipping item with invalid URL format: {m3u8_url}")
             skipped_count += 1
             continue

        # --- Filename generation/validation per item ---
        output_filename = None
        if desired_filename:
            if not desired_filename.lower().endswith('.mp4'):
                desired_filename += '.mp4'
            safe_filename = os.path.basename(desired_filename.replace('/', '_').replace('\\', '_'))
            # Basic check if filename is just dots or empty after sanitization
            if not safe_filename or set(safe_filename) == {'.'}:
                 print(f"Skipping item with invalid desired filename after sanitization: {desired_filename}")
                 skipped_count += 1
                 continue
            output_filename = safe_filename
        
        if not output_filename: # Fallback
            timestamp = time.strftime("%Y%m%d-%H%M%S")
            safe_part = m3u8_url.split('/')[-1].replace('.m3u8', '').replace('.', '_')
            output_filename = f"video_{safe_part}_{timestamp}_{started_count}.mp4" # Add count to avoid name collision in fallback

        output_path = os.path.join(app.config['DOWNLOAD_FOLDER'], output_filename)

        # --- Start download in background thread ---
        print(f"Starting background download for: URL='{m3u8_url}', Output='{output_path}'")
        thread = threading.Thread(target=run_download_thread, args=(m3u8_url, output_path), daemon=True)
        thread.start()
        started_count += 1

    # --- Redirect back with feedback ---
    if started_count > 0:
        flash(f'Started {started_count} downloads in the background. Check the "{DOWNLOAD_FOLDER}" directory for progress/completion.', 'info')
    if skipped_count > 0:
        flash(f'Skipped {skipped_count} invalid entries.', 'warning')
    if started_count == 0 and skipped_count == 0:
         flash('No valid download entries found to start.', 'error')

    return redirect(url_for('index'))


@app.route('/downloads/<filename>')
def serve_file(filename):
    """Serves the downloaded file."""
    print(f"Serving file: {filename} from {app.config['DOWNLOAD_FOLDER']}")
    try:
        return send_from_directory(app.config['DOWNLOAD_FOLDER'], filename, as_attachment=True)
    except FileNotFoundError:
        flash(f"Error: File '{filename}' not found.", 'error')
        return redirect(url_for('index'))

if __name__ == '__main__':
    # Use host='0.0.0.0' to make it accessible on your network
    app.run(debug=True, host='127.0.0.1', port=5000)