# Local voice output on Windows

MATRIX can use the speech voice already installed in Windows through the
Windows Speech API (SAPI). This is an optional local output path: no paid API,
account, cloud model, GPU, or additional Python package is required.

## Enable it

In PowerShell, before starting MATRIX:

```powershell
$env:MATRIX_TTS_PROVIDER = "windows-sapi"
matrix serve
```

The default is `none`, so starting MATRIX never unexpectedly turns on audio.
Set `MATRIX_TTS_PROVIDER=none` to disable the server voice path.

The adapter uses Windows PowerShell 5.1 and the installed `System.Speech`
assembly. It speaks through the PC's default audio device using the default
installed speech voice. Language and voice quality depend on which Windows
speech voices are installed. Neural-quality voices and Hindi support are not
guaranteed by this built-in adapter.

## How it works

- Speech is synthesized locally by the Windows speech engine.
- Text is passed to a fixed PowerShell script as UTF-8 JSON on standard input;
  the text is never inserted into a shell command or written to a file.
- MATRIX checks speech text for known credential patterns before playback.
- Barge-in and shutdown terminate the active speech process.
- If the local engine is unavailable, browser speech remains a separate
  fallback where the browser and its selected voice support it.

Server-side speech plays on the machine running MATRIX. It does not stream audio
back to a remote browser or Android device. Browser speech remains useful for
those clients, but its offline behavior depends on the browser/OS voice chosen.

## Limits and next work

This is a practical baseline voice, not a neural voice clone or a cinematic
JARVIS voice. Verify the installed voice, language, playback, and interruption
on the actual Windows PC. The CI suite tests process wiring and safety behavior;
it cannot prove that a physical speaker or installed voice pack works.

A future Piper or other neural local adapter can be added behind the same
`TtsEngine` interface after selecting a voice with a compatible license and a
measured RAM/CPU profile.
