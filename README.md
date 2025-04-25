# M3U8 Batch Downloader Web App

This is a simple web application built with Python and Flask that allows users to download HLS (M3U8) streaming videos and save them as MP4 files. It supports batch downloading by allowing users to input multiple M3U8 URLs and desired filenames.

## Features

*   Web-based interface for easy use.
*   Accepts M3U8 playlist URLs (handles master playlists by selecting the first available stream).
*   Allows specifying custom output filenames (optional).
*   Supports batch downloading of multiple URLs simultaneously.
*   Downloads are performed in the background using threading.
*   Uses `ffmpeg` for efficient stream copying (no re-encoding).

## Prerequisites

*   **Python 3**: Ensure Python 3 is installed on your system.
*   **ffmpeg**: This application relies on `ffmpeg` to combine the downloaded video segments. You must have `ffmpeg` installed and accessible in your system's PATH. You can download it from [https://ffmpeg.org/](https://ffmpeg.org/).
*   **pip**: Python's package installer, usually included with Python.

## Setup

1.  **Clone or Download:** Get the project files onto your local machine.
2.  **Navigate to Project Directory:** Open your terminal or command prompt and change to the directory containing the project files (e.g., `cd /path/to/m3u8-downloader-app`).
3.  **Install Dependencies:** Install the required Python packages using pip:
    ```bash
    pip install -r requirements.txt
    ```
4.  **(Optional) Create Download Directory:** The application will attempt to create a `downloads` directory automatically. If you encounter permission issues, create it manually in the project root.

## Running the Application

1.  **Start the Flask Server:** Run the following command in your terminal from the project directory:
    ```bash
    python app.py
    ```
2.  **Access the Web Interface:** Open your web browser and navigate to:
    [http://127.0.0.1:5000/](http://127.0.0.1:5000/)
3.  **Add Downloads:**
    *   Click the "Add Row" button to add entries.
    *   For each row, enter the M3U8 Playlist URL.
    *   Optionally, enter a desired filename (ending in `.mp4`). If left blank, a filename will be generated automatically.
4.  **Start Downloads:** Click the "Start All Downloads" button.
5.  **Monitor Progress:**
    *   The web page will show a confirmation message that downloads have started.
    *   Check the terminal where you ran `python app.py` for detailed logs and potential errors.
    *   Downloaded MP4 files will appear in the `downloads` folder within the project directory as they complete.

## Notes

*   **Background Processing:** Downloads run in background threads. The web UI doesn't currently show live progress for each download. Check the `downloads` folder and terminal logs.
*   **Error Handling:** Basic error handling is included, but complex stream protection or network issues might still cause downloads to fail. Check terminal logs for details.
*   **Resource Usage:** Running many downloads simultaneously can consume significant network bandwidth and CPU resources (especially during the `ffmpeg` combining step).
*   **ffmpeg Dependency:** Ensure `ffmpeg` is correctly installed and accessible in your system's PATH.