# Voice guide and voice input

## Guided tour (first login, demo, “Voice tour” button)

- Starts automatically the first time a user logs in or opens the demo. A welcome card offers **Start with voice / Start without voice / Skip** — audio only starts after that click, because browsers block autoplay.
- 14 steps across the pages (menu, language, new project, KPIs, tasks, Gantt, progress, budget, risks, team, reports, documents, assistant). Each step spotlights the element, shows the text and reads it aloud.
- Controls on every step: Back, Next, Pause/Play, Replay, Mute, Speed (0.75× / 1× / 1.25×), Skip. The current step is saved, so the tour resumes on the next page.
- Restart any time: top bar “Voice tour”, Help page, or Settings → “Show the guided tour again”.

## Which voice is used

The browser's own text-to-speech (`speechSynthesis`) with a voice that matches the interface language (`mr-IN`, `fr-FR`, `en-GB` …). Users can pick a specific voice and speed in Settings.

If the device has no voice for the language, the tour shows a note and the text stays on screen — the voice is never the only way to get the information. Microsoft Edge offers the most voices (including Marathi “online” voices); Chrome on Windows may not have a Marathi voice.

If the browser pauses speech after moving to another page, the step shows “Press Replay”.

## Voice input

- Microphone buttons on **New project** and **AI assistant** use the browser's speech recognition (Chrome and Edge) in the interface language.
- The recognised text is placed in the box so the user can check it before sending — it is never sent automatically.
- The microphone permission is only requested when the button is pressed.

## Optional: cloud voices

For identical voices on every device, add a server-side text-to-speech endpoint (for example Azure AI Speech or Google Cloud Text-to-Speech, both support Marathi) and call it from `MPvoice.speak` in `app/static/app.js` when `MPvoice.pick()` finds no local voice. Keep the provider key on the server.
