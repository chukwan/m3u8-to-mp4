import os
import threading
import time
import json
import re
import time
import threading
import os
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright, Error as PlaywrightError
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

# --- Playwright Scraping Function ---
# NOTE: This runs synchronously and launches a browser per request, which is inefficient
# for high load. Consider async/global context management for production.
def scrape_page_for_m3u8(page_url):
    """Uses Playwright to load a page, find title and first M3U8 network request."""
    m3u8_url_found = None
    page_title = None
    print(f"  [Playwright] Launching browser for {page_url}")

    # Using a context manager ensures the browser is closed properly
    with sync_playwright() as p:
        try:
            # Launch headless browser (Chromium is often a good default)
            # Use a realistic user agent
            user_agent = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(user_agent=user_agent)
            page = context.new_page()

            # --- Network Interception ---
            # Use a list to store the found URL (accessible from inner scope)
            found_urls = []
            def handle_request(route, request):
                 if ".m3u8" in request.url and not found_urls: # Find first m3u8 request
                     print(f"  [Playwright] Intercepted M3U8 request: {request.url}")
                     found_urls.append(request.url)
                 route.continue_() # Let the request proceed

            # Start routing *before* navigation
            page.route("**/*", handle_request)

            # --- Navigation and Waiting ---
            print(f"  [Playwright] Navigating to {page_url}")
            # Increased timeout for potentially slow pages
            page.goto(page_url, timeout=60000, wait_until='networkidle')
            # Alternative wait: page.wait_for_timeout(5000) # Wait fixed time after load

            print(f"  [Playwright] Page loaded. Checking for title and intercepted URL.")
            page_title = page.title()

            # Check if the handler found an M3U8 URL
            if found_urls:
                m3u8_url_found = found_urls[0]

            # Fallback: If network interception didn't find it, try simple HTML check again
            # (Sometimes it might be in the initial source after all)
            if not m3u8_url_found:
                 print("  [Playwright] M3U8 not found in network requests, checking HTML source as fallback...")
                 # Import here as it's only needed for the fallback
                 from bs4 import BeautifulSoup
                 content = page.content()
                 soup = BeautifulSoup(content, 'html.parser')
                 for tag in soup.find_all(['video', 'source']):
                     src = tag.get('src')
                     if src and '.m3u8' in src:
                         m3u8_url_found = urljoin(page_url, src)
                         print(f"  [Playwright] Found M3U8 in HTML <{tag.name} src>: {m3u8_url_found}")
                         break
                 if not m3u8_url_found:
                     for tag in soup.find_all('a'):
                         href = tag.get('href')
                         if href and '.m3u8' in href:
                             m3u8_url_found = urljoin(page_url, href)
                             print(f"  [Playwright] Found M3U8 in HTML <a href>: {m3u8_url_found}")
                             break

            browser.close()
            print(f"  [Playwright] Browser closed for {page_url}")

        except PlaywrightError as e:
            print(f"  [Playwright Error] Error during scraping {page_url}: {e}")
            # Ensure browser is closed if it exists and wasn't closed yet
            if 'browser' in locals() and browser.is_connected():
                 browser.close()
            return None, None # Return None on Playwright errors
        except Exception as e:
             print(f"  [Playwright Error] Unexpected error during scraping {page_url}: {e}")
             if 'browser' in locals() and browser.is_connected():
                 browser.close()
             return None, None # Return None on other errors

    return page_title, m3u8_url_found


# --- Filename Sanitization ---
def _sanitize_filename(name):
    """Removes invalid characters for filenames and limits length."""
    # Remove invalid characters (Windows example, adjust for cross-platform if needed)
    name = re.sub(r'[\\/*?:"<>|]', "", name)
    # Replace sequences of whitespace with a single underscore
    name = re.sub(r'\s+', '_', name)
    # Limit length (optional)
    max_len = 150
    if len(name) > max_len:
        name = name[:max_len]
    # Ensure it's not empty or just dots after sanitization
    if not name or set(name) == {'.'}:
        return None
    return name

@app.route('/download', methods=['POST'])
def handle_download():
    """Handles the batch download request by scraping pages."""
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

    # Remove requests session setup, Playwright handles its own context
    # session = requests.Session() ...

    for item in batch_items:
        if not isinstance(item, dict) or 'page_url' not in item:
            print(f"Skipping invalid batch item format: {item}")
            skipped_count += 1
            continue

        page_url = item['page_url'].strip()
        if not page_url or not page_url.startswith(('http://', 'https://')):
            print(f"Skipping invalid page URL: {page_url}")
            skipped_count += 1
            continue

        print(f"Processing page: {page_url}")
        m3u8_url_found = None
        page_title = None
        output_filename = None

        # --- Scrape using Playwright ---
        try:
            page_title, m3u8_url_found = scrape_page_for_m3u8(page_url)

            if not m3u8_url_found:
                print(f"  Failed to find M3U8 link for {page_url} after scraping.")
                skipped_count += 1
                continue

            # --- Generate Filename ---
            if page_title:
                output_filename = _sanitize_filename(page_title)
                if output_filename:
                    output_filename += ".mp4"
                else:
                    print(f"  Page title '{page_title}' resulted in invalid filename after sanitization.")

            # Fallback filename
            if not output_filename:
                timestamp = time.strftime("%Y%m%d-%H%M%S")
                safe_part = m3u8_url_found.split('/')[-1].replace('.m3u8', '').replace('.', '_')
                output_filename = f"video_{safe_part}_{timestamp}_{started_count}.mp4"
                print(f"  Using fallback filename: {output_filename}")

            output_path = os.path.join(app.config['DOWNLOAD_FOLDER'], output_filename)

            # --- Start Download Thread ---
            print(f"  Starting background download for: M3U8='{m3u8_url_found}', Output='{output_path}'")
            thread = threading.Thread(target=run_download_thread, args=(m3u8_url_found, output_path), daemon=True)
            thread.start()
            started_count += 1

        except Exception as e:
            # Catch errors during the scraping call itself or filename generation
            print(f"  Error processing page {page_url}: {e}")
            import traceback
            traceback.print_exc()
            skipped_count += 1
            continue

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