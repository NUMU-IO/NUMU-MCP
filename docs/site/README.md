# Publishing the merchant guide to docs.numueg.app

`connect-your-ai.md` is a ready-to-publish **VitePress** page for the merchant
docs (`docs.numueg.app`). The docs site source (`numu-docs`) lives outside this
repo and deploys as static files to the DigitalOcean droplet, so add the page
there and rebuild.

## 1. Add the page

Copy `connect-your-ai.md` into the `numu-docs` content tree, e.g.:

```
numu-docs/
└── ai/
    └── connect-your-ai.md      ← this file
```

(Anywhere works; pick the folder that matches your structure. A new top-level
section "AI" reads well next to API / Workflows.)

## 2. Wire it into the nav + sidebar

In `.vitepress/config.*` add a nav item and a sidebar group. Example:

```ts
export default defineConfig({
  themeConfig: {
    nav: [
      // …existing items (Getting Started, Theme Engine, SDK, …)
      { text: 'AI', link: '/ai/connect-your-ai' },
    ],
    sidebar: {
      '/ai/': [
        {
          text: 'AI (MCP)',
          items: [
            { text: 'Connect your AI to your store', link: '/ai/connect-your-ai' },
          ],
        },
      ],
    },
  },
})
```

## 3. Build & deploy to the droplet

From the `numu-docs` project:

```bash
npm install            # first time
npm run docs:build     # outputs .vitepress/dist (static site)
```

Then sync the built static files to the droplet's web root (adjust path/host to
your nginx config):

```bash
rsync -avz --delete .vitepress/dist/ \
  deploy@<DROPLET_IP>:/var/www/docs.numueg.app/
# nginx serves it; no reload needed for static files, but if you changed nginx:
# ssh deploy@<DROPLET_IP> 'sudo nginx -t && sudo systemctl reload nginx'
```

If `numu-docs` has a deploy script/CI (e.g. `npm run deploy` or a GitHub Action),
prefer that over manual rsync.

## Notes on accuracy

- The page documents **two** ways to mint a Personal Access Token: a dashboard
  button (**recommended**, not built yet) and the API (works today). Once the
  dashboard "Access Tokens" page exists in `numo-merchant-hub`, the dashboard
  path becomes the primary flow and the API becomes the advanced option.
- It mentions an optional **hosted** remote MCP endpoint (e.g.
  `https://mcp.numueg.app/mcp`). Only advertise that once it's actually deployed
  (see `docs/DEPLOY.md`); otherwise merchants use the local install.
