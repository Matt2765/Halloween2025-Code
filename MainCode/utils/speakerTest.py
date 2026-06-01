from control.audio_manager import play_audio
import time as t

def testAudio():
    play_audio("speaker gangway")
    t.sleep(2)
    play_audio("gangway", "waterWave01.wav", gain=1)

    t.sleep(3)

    play_audio("speaker cargo hold")
    t.sleep(2)
    play_audio("cargoHold", "waterWave01.wav", gain=1)

    t.sleep(3)

    play_audio("speaker quarterdeck")
    t.sleep(2)
    play_audio("quarterdeck", "waterWave01.wav", gain=1)

    t.sleep(3)

    play_audio("speaker treasure room")
    t.sleep(2)
    play_audio("treasureRoom", "waterWave01.wav", gain=1)

    t.sleep(3)

    play_audio("speaker graveyard")
    t.sleep(2)
    play_audio("graveyard", "waterWave01.wav", gain=1)

    t.sleep(3)