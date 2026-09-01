# IntentCart — Remaining Submission Steps

All internal code, tests, public-development evaluation, and recording guidance
are complete. The selected immutable release is
`submission-v2`; `submission-v1` remains the comparison control. Resolve the
exact selected commit with `git rev-list -n 1 submission-v2`. Only the following
account actions remain.

## 1. Record and review the team-made video

1. Check out `submission-v2` and follow the timed sequence in `DEMO_SCRIPT.md`.
2. Record the actual terminal demo, 33 passing tests, v1/v2 comparison, and
   official v2 metrics. Keep `.env` closed and never show an API key.
3. Keep the final recording under three minutes unless the submission rules
   explicitly allow longer.
4. Review the complete export for readable text, correct audio, accurate public
   metrics, and absence of private paths, credentials, third-party footage,
   product imagery, logos, or copyrighted music.
5. Keep the video outside Git or under ignored `docs/audits/`.

## 2. Publish the video on YouTube

1. Upload the reviewed team-made video.
2. Use the title `IntentCart — End-to-End Conversational Shopping Copilot`.
3. Paste the suggested description from `DEMO_SCRIPT.md`.
4. Add accurate English subtitles and a team-made thumbnail if desired.
5. Set visibility to **Public**, finish publishing, and copy the public URL.
6. Open that URL in a signed-out/private browser window and play the beginning
   and end.

## 3. Complete the Devpost entry

1. Paste the content of `DEVPOST.md` into the project description. It is limited
   to the requested problem fit, implementation, evidence, tools, APIs,
   libraries, datasets/assets, limitations, and contributions; it does not add
   an inspiration section.
2. Replace the demo-video instruction in its **Public links** section with the
   public YouTube URL.
3. Put the same URL in Devpost's video field.
4. Use `https://github.com/shang-yi-qian/shopping-copilot` as the public code
   repository.
5. Preview the entry and confirm that the repository and video links open.
6. Submit the entry, then open the published Devpost page while signed out.

## 4. Retain submission evidence

Record the following in the private copy of
`docs/audits/submission-package/EXTERNAL_PUBLICATION_RECORD.md`:

- public YouTube URL;
- public Devpost URL;
- submission date/time and confirmation identifier or screenshot;
- release tag `submission-v2` and its resolved exact commit; and
- confirmation that all links worked while signed out.

Do not commit account screenshots, private identifiers, or credentials.

## 5. Run the private final set only after release

When the organizer releases the separate 800-session final package, use a clean
checkout of `submission-v2` and the unchanged official evaluator. Save its JSON
and environment record, but do not modify the Agent or tune against final labels.
