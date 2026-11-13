# M3U Manager — Vercel version

## Project files

- `index.html` — frontend (M3U upload, channel cards, favourites, search, exports)
- `api/check.py` — Vercel Python Function for HTTP/HTTPS link checks
- `vercel.json` — configures the checker function duration

## Deploy to Vercel

### Option A: GitHub (recommended)

1. Extract this ZIP.
2. Create a new GitHub repository.
3. Upload the contents of this folder to the repository root.
   `index.html`, `vercel.json`, and the `api` folder must be at the top level.
4. In Vercel, choose **Add New → Project**.
5. Import the GitHub repository.
6. Leave Framework Preset as **Other**. No build command is needed.
7. Deploy.

### Option B: Vercel CLI

Install Node.js, then run:

    npm install -g vercel

Open a terminal in this project folder and run:

    vercel

Follow the prompts. For the production deployment, run:

    vercel --prod

## Important notes

- The frontend uploads and parses the M3U file in the browser. The playlist itself is not uploaded to Vercel.
- Favourites are saved in the browser's localStorage on the device/browser where they were selected. They do not sync across devices.
- Link checking uses `/api/check`, a Python Vercel Function. It checks reachability, not guaranteed playback.
- Some IPTV providers block cloud/datacentre IP addresses, require tokens/headers, or restrict regions. Such streams may show failed/uncertain even if they play in VLC at home.
- Only HTTP/HTTPS URLs are checked. RTMP/RTSP/UDP entries can still be favourited and exported, but are not tested by this checker.
- Vercel function duration and usage are subject to the limits of your Vercel plan.
- The checker rejects private/local network destinations to reduce server-side request forgery risk.
