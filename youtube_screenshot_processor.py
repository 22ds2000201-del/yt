#!/usr/bin/env python3
"""
YouTube Video Screenshot Capture Tool for GitHub Actions
Processes YouTube videos to extract screenshots, transcripts, and generate PDFs
"""

import os
import sys
import re
import subprocess
import argparse
import tempfile
import shutil
from pathlib import Path
import json
import hashlib
from PIL import Image
import numpy as np
from datetime import timedelta
import textwrap

def sanitize_filename(filename, max_length=100):
    """Remove invalid characters from filename"""
    invalid_chars = '<>:"/\\|?*'
    for char in invalid_chars:
        filename = filename.replace(char, '')
    filename = filename.strip('. ')
    filename = filename.replace(' ', '_')
    if len(filename) > max_length:
        filename = filename[:max_length]
    return filename

def format_time(seconds):
    """Convert seconds to readable format"""
    return str(timedelta(seconds=int(seconds)))

def get_video_info(url):
    """Get video information using yt-dlp"""
    try:
        cmd = ['yt-dlp', '--dump-json', '--no-playlist', url]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=30)
        info = json.loads(result.stdout)

        subtitles_available = bool(info.get('subtitles', {})) or bool(info.get('automatic_captions', {}))

        return {
            'title': info.get('title', 'untitled'),
            'duration': info.get('duration', 0),
            'uploader': info.get('uploader', 'Unknown'),
            'view_count': info.get('view_count', 0),
            'subtitles_available': subtitles_available
        }
    except Exception as e:
        print(f"Error getting video info: {e}")
        return None

def download_video_and_transcript(url, video_path, transcript_path, force_hd=True):
    """Download YouTube video and transcript"""
    try:
        format_options = [
            'bestvideo[height>=1080][ext=mp4]+bestaudio[ext=m4a]/best[height>=1080][ext=mp4]',
            'bestvideo[height>=720][ext=mp4]+bestaudio[ext=m4a]/best[height>=720][ext=mp4]',
            'best[ext=mp4]/best'
        ]

        cmd = [
            'yt-dlp',
            '-f', '/'.join(format_options),
            '--no-playlist',
            '--merge-output-format', 'mp4',
            '-o', video_path,
            '--write-auto-subs',
            '--write-subs',
            '--sub-lang', 'en',
            '--convert-subs', 'srt',
            url
        ]

        print("📥 Downloading video...")
        subprocess.run(cmd, check=True)

        # Check for transcript
        video_dir = os.path.dirname(video_path)
        video_base = os.path.splitext(os.path.basename(video_path))[0]

        subtitle_patterns = [
            f"{video_base}.en.srt",
            f"{video_base}.en.vtt",
            f"{video_base}.srt",
            f"{video_base}.vtt"
        ]

        transcript_found = False
        for pattern in subtitle_patterns:
            potential_file = os.path.join(video_dir, pattern)
            if os.path.exists(potential_file):
                convert_srt_to_text(potential_file, transcript_path)
                transcript_found = True
                break

        return True, transcript_found

    except Exception as e:
        print(f"❌ Error downloading video: {e}")
        return False, False

def convert_srt_to_text(srt_file, text_file):
    """Convert SRT subtitle file to plain text"""
    try:
        with open(srt_file, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()

        transcript_lines = []
        current_text = []

        for line in lines:
            line = line.strip()
            if line and not line.isdigit() and '-->' not in line:
                line = re.sub('<[^<]+?>', '', line)
                current_text.append(line)
            elif not line and current_text:
                transcript_lines.append(' '.join(current_text))
                current_text = []

        if current_text:
            transcript_lines.append(' '.join(current_text))

        with open(text_file, 'w', encoding='utf-8') as f:
            f.write("VIDEO TRANSCRIPT\n")
            f.write("=" * 50 + "\n\n")
            full_text = ' '.join(transcript_lines)
            full_text = re.sub(r'\s+', ' ', full_text)
            wrapped_text = textwrap.fill(full_text, width=80)
            f.write(wrapped_text)

        return True
    except Exception as e:
        print(f"❌ Error converting transcript: {e}")
        return False

def extract_high_quality_screenshots(video_path, output_dir, interval, title_prefix, quality='highest'):
    """Extract screenshots from video"""
    try:
        os.makedirs(output_dir, exist_ok=True)

        # Get video duration
        cmd = [
            'ffprobe', '-v', 'error',
            '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1',
            video_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        duration = float(result.stdout.strip())

        print(f"📹 Video duration: {duration:.1f} seconds")
        print(f"📸 Extracting screenshots every {interval} seconds...")

        screenshots_taken = 0
        current_time = 0
        screenshot_files = []

        while current_time <= duration:
            time_str = f"{int(current_time):04d}s"

            if quality == 'highest':
                output_file = os.path.join(output_dir, f"{title_prefix}_{time_str}.png")
                cmd = [
                    'ffmpeg', '-ss', str(current_time),
                    '-i', video_path, '-vframes', '1',
                    '-vf', 'scale=iw:ih', '-y', output_file
                ]
            else:
                output_file = os.path.join(output_dir, f"{title_prefix}_{time_str}.jpg")
                cmd = [
                    'ffmpeg', '-ss', str(current_time),
                    '-i', video_path, '-vframes', '1',
                    '-q:v', '1', '-y', output_file
                ]

            subprocess.run(cmd, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, check=True)

            screenshots_taken += 1
            screenshot_files.append(output_file)

            progress = (current_time / duration) * 100
            print(f"  [{progress:5.1f}%] Screenshot at {format_time(current_time)}")

            current_time += interval

        return screenshots_taken, screenshot_files

    except Exception as e:
        print(f"❌ Error extracting screenshots: {e}")
        return 0, []

def get_image_hash(image_path):
    """Calculate hash of an image"""
    try:
        with open(image_path, 'rb') as f:
            return hashlib.sha256(f.read()).hexdigest()
    except Exception:
        return None

def remove_duplicate_screenshots(screenshot_files):
    """Remove duplicate screenshots"""
    if len(screenshot_files) <= 1:
        return 0

    hashes = {}
    duplicates_removed = 0

    for img_path in screenshot_files:
        img_hash = get_image_hash(img_path)
        if img_hash:
            if img_hash in hashes:
                try:
                    os.remove(img_path)
                    duplicates_removed += 1
                except Exception:
                    pass
            else:
                hashes[img_hash] = img_path

    return duplicates_removed

def create_hd_pdf(images_dir, output_pdf, dpi=600):
    """Create high-quality PDF from screenshots"""
    try:
        import img2pdf
        
        image_files = sorted([
            f for f in Path(images_dir).iterdir()
            if f.suffix.lower() in ['.png', '.jpg', '.jpeg']
        ])

        if not image_files:
            print("⚠️  No images found for PDF creation")
            return False

        print(f"📄 Creating PDF with {len(image_files)} images at {dpi} DPI...")

        with open(output_pdf, 'wb') as f:
            f.write(img2pdf.convert([str(img) for img in image_files]))

        print(f"✅ PDF created: {output_pdf}")
        return True

    except ImportError:
        print("⚠️  img2pdf not installed, trying alternative method...")
        return create_pdf_with_pillow(images_dir, output_pdf, dpi)
    except Exception as e:
        print(f"❌ Error creating PDF: {e}")
        return False

def create_pdf_with_pillow(images_dir, output_pdf, dpi=600):
    """Create PDF using Pillow as fallback"""
    try:
        image_files = sorted([
            f for f in Path(images_dir).iterdir()
            if f.suffix.lower() in ['.png', '.jpg', '.jpeg']
        ])

        if not image_files:
            return False

        images = []
        for img_path in image_files:
            img = Image.open(img_path)
            if img.mode == 'RGBA':
                img = img.convert('RGB')
            images.append(img)

        if images:
            images[0].save(
                output_pdf, 'PDF', resolution=dpi, save_all=True,
                append_images=images[1:] if len(images) > 1 else []
            )
            print(f"✅ PDF created: {output_pdf}")
            return True

        return False

    except Exception as e:
        print(f"❌ Error creating PDF with Pillow: {e}")
        return False

def process_video(url, interval, output_dir='.', quality='highest',
                 pdf_dpi=600, keep_video=False, no_transcript=False, no_pdf=False):
    """Main processing function"""

    # Get video info
    print(f"🔍 Fetching video information...")
    video_info = get_video_info(url)
    if not video_info:
        return False

    safe_title = sanitize_filename(video_info['title'])

    print(f"\n{'='*60}")
    print(f"📺 Video: {video_info['title']}")
    print(f"⏱️  Duration: {format_time(video_info['duration'])}")
    print(f"👤 Uploader: {video_info['uploader']}")
    print(f"{'='*60}\n")

    # Create directories
    base_dir = Path(output_dir)
    video_dir = base_dir / safe_title
    images_dir = video_dir / 'images'
    images_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as temp_dir:
        video_path = os.path.join(temp_dir, 'video.mp4')
        transcript_path = video_dir / f"{safe_title}_transcript.txt"

        # Download
        video_success, transcript_success = download_video_and_transcript(
            url, video_path, transcript_path if not no_transcript else None
        )

        if not video_success:
            print("❌ Error: Failed to download video")
            return False

        print(f"\n✅ Video downloaded successfully!")

        # Extract screenshots
        screenshot_count, screenshot_files = extract_high_quality_screenshots(
            video_path, images_dir, interval, safe_title, quality
        )

        if screenshot_count > 0:
            print(f"\n✅ Extracted {screenshot_count} screenshots")

            # Remove duplicates
            duplicates = remove_duplicate_screenshots(screenshot_files)
            if duplicates > 0:
                print(f"🔄 Removed {duplicates} duplicates")
                print(f"   Final: {screenshot_count - duplicates} unique screenshots")

            # Create PDF
            if not no_pdf:
                pdf_path = video_dir / f"{safe_title}_HD.pdf"
                create_hd_pdf(images_dir, pdf_path, pdf_dpi)

            # Keep video if requested
            if keep_video:
                final_video_path = video_dir / f"{safe_title}.mp4"
                shutil.copy2(video_path, final_video_path)

            # Summary
            print(f"\n{'='*60}")
            print(f"✅ COMPLETED SUCCESSFULLY!")
            print(f"{'='*60}")
            print(f"📁 Output: {video_dir.absolute()}")
            print(f"🖼️  Screenshots: {len(screenshot_files) - duplicates}")
            if not no_pdf:
                print(f"📄 PDF: {pdf_path.name}")
            if transcript_success:
                print(f"📝 Transcript: {transcript_path.name}")
            print(f"{'='*60}\n")

            # Set GitHub Actions output
            if os.getenv('GITHUB_OUTPUT'):
                with open(os.getenv('GITHUB_OUTPUT'), 'a') as f:
                    f.write(f"output_dir={video_dir.absolute()}\n")
                    f.write(f"pdf_file={pdf_path if not no_pdf else ''}\n")
                    f.write(f"transcript_file={transcript_path if transcript_success else ''}\n")
                    f.write(f"screenshot_count={len(screenshot_files) - duplicates}\n")

            return True
        else:
            print("❌ Error: No screenshots extracted")
            return False

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='YouTube Screenshot Tool for GitHub Actions')
    parser.add_argument('url', help='YouTube video URL')
    parser.add_argument('interval', type=int, help='Interval in seconds')
    parser.add_argument('--output-dir', default='./output', help='Output directory')
    parser.add_argument('--quality', choices=['high', 'highest'], default='highest')
    parser.add_argument('--pdf-dpi', type=int, default=600)
    parser.add_argument('--keep-video', action='store_true')
    parser.add_argument('--no-transcript', action='store_true')
    parser.add_argument('--no-pdf', action='store_true')

    args = parser.parse_args()

    success = process_video(
        args.url, args.interval, args.output_dir,
        args.quality, args.pdf_dpi, args.keep_video,
        args.no_transcript, args.no_pdf
    )

    sys.exit(0 if success else 1)
