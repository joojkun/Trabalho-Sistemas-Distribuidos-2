const http = require("node:http");
const { readFile } = require("node:fs");
const path = require("node:path");

const ROOT = __dirname;
const PORT = Number(process.env.FRONTEND_PORT || 8080);
const API_BASE_URL = process.env.API_BASE_URL || "http://localhost:5000";
const FILES = new Map([
  ["/", ["index.html", "text/html; charset=utf-8"]],
  ["/index.html", ["index.html", "text/html; charset=utf-8"]],
  ["/app.js", ["app.js", "text/javascript; charset=utf-8"]],
  ["/styles.css", ["styles.css", "text/css; charset=utf-8"]],
]);

const server = http.createServer((request, response) => {
  if (request.method !== "GET" && request.method !== "HEAD") {
    response.writeHead(405, { Allow: "GET, HEAD" });
    response.end("Method not allowed");
    return;
  }

  const pathname = new URL(request.url, "http://localhost").pathname;
  if (pathname === "/config.js") {
    response.writeHead(200, {
      "Content-Type": "text/javascript; charset=utf-8",
      "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
    });
    response.end(`window.API_BASE_URL = ${JSON.stringify(API_BASE_URL)};`);
    return;
  }

  const file = FILES.get(pathname);
  if (!file) {
    response.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
    response.end("Not found");
    return;
  }

  const [filename, contentType] = file;
  readFile(path.join(ROOT, filename), (error, content) => {
    if (error) {
      console.error(`Unable to read ${filename}:`, error);
      response.writeHead(500, { "Content-Type": "text/plain; charset=utf-8" });
      response.end("Unable to load the frontend");
      return;
    }
    response.writeHead(200, {
      "Content-Type": contentType,
      "Cache-Control": "no-cache",
      "X-Content-Type-Options": "nosniff",
    });
    response.end(request.method === "HEAD" ? undefined : content);
  });
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(`Frontend disponível em http://localhost:${PORT}`);
});
