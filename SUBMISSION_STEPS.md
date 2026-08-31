# IntentCart — Remaining Submission Steps

All internal code, tests, public-development evaluation, documentation, and the
upload-ready demo are complete. The immutable evaluated release is
`submission-v1` at commit
`50bc78ec108966585211d8980efbb78697293495`. Only the following account actions
remain.

## 1. Review the final video files

From the repository root, open the upload-ready artifacts:

```bash
open docs/audits/IntentCart_End_to_End_Demo.mp4
open docs/audits/IntentCart_Demo_Thumbnail.png
```

The final video is 2:53.8, 1920×1080, and contains original rendered evidence
with narration. Its SHA-256 is
`f06c5b9d91d3d129daf28c3659fe7d66077499dc88fab22ccbfc77159e2b2ac3`.

## 2. Publish the video on YouTube

1. Upload `docs/audits/IntentCart_End_to_End_Demo.mp4`.
2. Use the title `IntentCart — End-to-End Conversational Shopping Copilot`.
3. Paste the suggested description from `DEMO_SCRIPT.md`.
4. Upload `docs/audits/IntentCart_Demo_Thumbnail.png` as the thumbnail.
5. Add `docs/audits/IntentCart_End_to_End_Demo.srt` as English subtitles.
6. Set visibility to **Public**, finish publishing, and copy the public URL.
7. Open that URL in a signed-out/private browser window and play the beginning
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
- release tag and exact commit above; and
- confirmation that all links worked while signed out.

Do not commit account screenshots, private identifiers, or credentials.

## 5. Run the private final set only after release

When the organizer releases the separate 800-session final package, use a clean
checkout of `submission-v1` and the unchanged official evaluator. Save its JSON
and environment record, but do not modify the Agent or tune against final labels.
