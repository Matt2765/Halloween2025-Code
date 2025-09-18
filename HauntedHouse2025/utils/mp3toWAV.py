import os
from tkinter import Tk, filedialog
from pydub import AudioSegment

def convert_mp3_to_wav(folder_path):
    for filename in os.listdir(folder_path):
        if filename.lower().endswith(".mp3"):
            mp3_path = os.path.join(folder_path, filename)
            wav_path = os.path.join(folder_path, os.path.splitext(filename)[0] + ".wav")
            try:
                sound = AudioSegment.from_mp3(mp3_path)
                sound.export(wav_path, format="wav")
                log_event(f"Converted: {filename} -> {os.path.basename(wav_path)}")
            except Exception as e:
                log_event(f"Failed to convert {filename}: {e}")

def main():
    root = Tk()
    root.withdraw()  # hide root window
    folder_selected = filedialog.askdirectory(title="Select Folder with MP3s")
    if folder_selected:
        convert_mp3_to_wav(folder_selected)
    else:
        log_event("No folder selected.")

if __name__ == "__main__":
    main()
