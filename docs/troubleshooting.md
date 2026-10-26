# Troubleshooting

Each section starts with what you see. Hover a page to see its chip in the top right corner. Hovering the chip's status shows the full error.

## The chip says it has no access to a host

The chip reads "No access to" and a host name. The extension has no permission for the host the image comes from, so it cannot fetch the image.

1. Click the Yomikae toolbar icon.
2. If the popup names the missing hosts, click **Allow access** and accept Chrome's prompt.
3. If it doesn't, switch **Translate** off for the site and on again, and allow access when Chrome asks.

The page retries by itself once access is granted. You don't need to reload the tab.

If the site serves its images from a different domain and has no adapter, the popup can't ask for that domain. [sites.md](sites.md#other-sites) lists this limit.

## The chip says "Waiting for server…"

The Yomikae server is not running, or the LLM server behind it is not answering. Hovering the chip shows which.

1. Click the toolbar icon. The popup shows whether the server is running, and the command to start it if it isn't.
2. Start the server. If it is already running, check that LM Studio has the model loaded and its server started:

	```sh
	lms ps
	lms server start
	```

Waiting pages continue by themselves. The extension checks the server after 2, 4, 8, and 16 seconds, then every 30 seconds, and sends the pages in their usual order once it answers.

## The chip says "Translation failed"

Hover the chip to read the error, then check the cause that matches:

- **"Local server timed out."** The server took more than 180 s for one page. A model that thinks before it answers can take that long, and so can a model that no longer fits on the GPU. Check that `YOMIKAE_OPENAI_REASONING_EFFORT` is `none` or unset, and see [performance.md](performance.md#keep-the-gpu-from-filling-up).
- **"Image fetch failed"**, with a status code or a reason such as "Image download stalled." The site refused the image, or it stopped sending it. Reload the tab and click **Retry**.
- **"translator at … returned 400 after 3 tries: {"error":"Channel Error"}"**. LM Studio failed the request three times in a row. The server resends a Channel Error twice on its own, so one that reaches the chip is not a one-off. Click **Retry**. If it keeps failing, restart LM Studio's server and look in its server log for `Engine protocol predict request failed`.
- **"translator at … returned 400"** with any other body. The translator rejected the request itself, and a retry gets the same answer. Check `YOMIKAE_OPENAI_MODEL` and the endpoint's own log.
- **"Rendered page fetch failed"**, or any other error from the server. Look in the server's terminal for a traceback.

Click **Retry** in the chip to try again. A chip that says **Can't translate** has an error that a retry won't fix, such as an image the server cannot decode or an image the site no longer has.

If one page gets the same error twice, report it, as [Report a bad page](#report-a-bad-page) explains.

## The Translate chip never appears

Check these in order:

- **The site is switched on.** Click the toolbar icon and check that **Translate** is on for the site. Without it, the extension does nothing on the site.
- **The mode.** The **Translate** chip appears only in **Only images I click Translate on**. In **Every page, automatically**, pages translate on their own, and hovering one shows its progress. On a site without its own adapter, automatic mode translates only images drawn at least 300 px wide from files at least 500 px on each side. To translate a smaller image, switch the site to click mode.
- **The image size.** On a site without its own adapter, the chip appears only on images at least 200 px on each side. Icons, avatars, and emoji never get it.

## The screen glitches or stutters while you read

The GPU is nearly full. The LLM and the server together can leave too little graphics memory for the desktop. Check with `nvidia-smi`, and keep 1 to 2 GB free. [performance.md](performance.md#keep-the-gpu-from-filling-up) lists ways to free memory, such as a shorter LM Studio context, `YOMIKAE_DETECT_DEVICE=cpu`, or `YOMIKAE_LOW_VRAM=1`.

## Sites are switched off after you moved the extension folder

Chrome derives the ID of an unpacked extension from the path of its folder. A moved folder is a new extension to Chrome, with no stored sites and no granted access.

1. Open `chrome://extensions` and remove the old entry.
2. Click **Load unpacked** and select the folder in its new place.
3. Switch each site on again in the popup.

An ordinary reload of the extension keeps the sites. The extension registers its script again for every site whose access is still granted.

## A tab open during an extension reload behaves oddly

After a reload, the extension injects its script again into open tabs of the sites you switched on, and those tabs keep translating. If a tab still misbehaves, for example a chip that never changes, reload the tab once.

## The text in a tiny bubble is too small

This is by design. Lettering always stays inside its bubble, even when that makes it small, rather than spilling over the outline or the art. [decisions.md](decisions.md#lettering-stays-inside-the-bubble) explains why. To read the line, hold **Alt** to see the original page, and let go to go back to English.

If the text is small in a bubble that has plenty of room, report the page.

## Report a bad page

A report saves everything needed to reproduce a problem with one page.

1. Hover the page and click **Report** in its chip, or press **Alt+R** with the cursor over the page.
2. Click each bubble that is wrong. Shift-click adds another bubble. To mark text the extension missed, drag a box around it.
3. Type a short note on what is wrong, such as "name spelled two ways" or "Japanese still visible".
4. Press Enter. Press Esc to cancel.

A toast shows the report id. The server saves the report to `~/.local/share/yomikae/reports/<report id>/`, or under `YOMIKAE_REPORTS_DIR` if you set it. Each report holds the original page, the render and the server's result if the page was translated, an image of the page with your marks if you made any, and `report.json` with the page and image URLs and your note. [contract.md](contract.md#post-v1reports) lists every file.

A report works whether the page translated, failed, or is still translating. Reports stay on your computer. To share one, check that the page and the URLs in it are fine to share first.
