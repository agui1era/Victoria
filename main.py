from dotenv import load_dotenv
import queue, json, tempfile, wave, os
import sounddevice as sd
import numpy as np
import vosk
from gtts import gTTS
import simpleaudio as sa
from openai import OpenAI

# Config

load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
HOTWORD = "victoria"
SAMPLE_RATE = 16000
MODEL_PATH = "model"

q = queue.Queue()
model = vosk.Model(MODEL_PATH)
client = OpenAI(api_key=OPENAI_API_KEY)

def callback(indata, frames, time, status):
    q.put(bytes(indata))

def play_audio(file):
    wave_obj = sa.WaveObject.from_wave_file(file)
    play_obj = wave_obj.play()
    play_obj.wait_done()

def record_audio(filename, duration=5):
    print("🎙️ Grabando...")
    recording = sd.rec(int(duration * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype='int16')
    sd.wait()
    wave.write(filename, SAMPLE_RATE, recording)
    print(f"💾 Audio guardado en {filename}")

def transcribe(file):
    print("🧠 Transcribiendo con Whisper...")
    with open(file, "rb") as f:
        transcription = client.audio.transcriptions.create(model="gpt-4o-mini-transcribe", file=f)
    return transcription.text

def ask_gpt(prompt):
    print("💬 Enviando a GPT...")
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}]
    )
    text = response.choices[0].message.content
    print("🤖 GPT:", text)
    return text

def speak(text):
    print("🔊 Reproduciendo respuesta...")
    tts = gTTS(text=text, lang='es')
    temp = tempfile.mktemp(suffix=".wav")
    tts.save(temp)
    play_audio(temp)
    os.remove(temp)

def main():
    print("Victoria escuchando... 🦊")
    with sd.RawInputStream(samplerate=SAMPLE_RATE, blocksize=8000, dtype='int16',
                           channels=1, callback=callback):
        rec = vosk.KaldiRecognizer(model, SAMPLE_RATE)
        while True:
            data = q.get()
            if rec.AcceptWaveform(data):
                text = json.loads(rec.Result())["text"]
                if HOTWORD in text.lower():
                    print(f"🔥 Hotword detectada: {HOTWORD}")
                    tmpfile = tempfile.mktemp(prefix="victoria_", suffix=".wav")
                    record_audio(tmpfile, duration=5)
                    prompt = transcribe(tmpfile)
                    respuesta = ask_gpt(prompt)
                    speak(respuesta)

if __name__ == "__main__":
    main()