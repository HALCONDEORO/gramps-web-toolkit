"""Shared binary filename classification. No file reads or network calls."""
import os

BINARY_EXT = {'.jpg', '.jpeg', '.png', '.gif', '.tif', '.tiff', '.webp', '.bmp', '.heic', '.jp2', '.pdf',
              '.zip', '.tar', '.gz', '.tgz', '.7z', '.mp3', '.m4a', '.ogg', '.wav', '.mp4', '.mov', '.djvu'}

def is_binary_name(path):
    return os.path.splitext(path)[1].lower() in BINARY_EXT

