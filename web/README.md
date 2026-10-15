# Macro Engine web

Vite + React UI for the local tracker. The Python API lives in
`macro_engine/http_api.py` and must be running for `/api` to answer.

```bash
npm install
npm run dev      # http://127.0.0.1:5173, proxies /api to port 8787
npm run build    # output in dist/, served by the API process
```
