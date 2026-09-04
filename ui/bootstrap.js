/* Runs the CSED engine in the browser.
 *
 * GitHub Pages serves files, not processes, so there is no server to answer /api/*.
 * Instead a Python runtime is loaded, the engine package and its database are written
 * into its filesystem, and fetch is intercepted so the existing page talks to it
 * unchanged. The engine source is the same file the local server imports, so a roadmap
 * generated here is the roadmap the server would produce.
 */
(function () {
  const PYODIDE = "https://cdn.jsdelivr.net/pyodide/v0.26.4/full/";
  const MODULES = ["__init__.py", "state.py", "data.py", "engine.py", "api.py"];

  const panel = document.createElement("div");
  panel.id = "boot";
  panel.innerHTML =
    '<div class="boot-inner"><b>Starting the scheduler</b>' +
    '<p id="boot-step">loading the Python runtime…</p>' +
    '<div class="boot-bar"><i id="boot-fill"></i></div>' +
    '<p class="boot-note">The planner runs entirely in this tab. Nothing you enter ' +
    "is uploaded.</p></div>";
  document.body.appendChild(panel);

  const step = (text, pct) => {
    const el = document.getElementById("boot-step");
    const fill = document.getElementById("boot-fill");
    if (el) el.textContent = text;
    if (fill) fill.style.width = pct + "%";
  };

  async function load(src) {
    return new Promise((ok, fail) => {
      const s = document.createElement("script");
      s.src = src;
      s.onload = ok;
      s.onerror = () => fail(new Error("could not load " + src));
      document.head.appendChild(s);
    });
  }

  window.__APTG_READY__ = (async () => {
    step("loading the Python runtime…", 10);
    await load(PYODIDE + "pyodide.js");
    const py = await loadPyodide({ indexURL: PYODIDE });

    // Pyodide unvendors sqlite3 from the standard library; the engine reads its
    // catalogue through it, so pull it in before importing anything.
    step("loading the database driver…", 32);
    await py.loadPackage("sqlite3");

    step("fetching the engine…", 45);
    py.FS.mkdirTree("/aptg/aptg_engine");
    for (const name of MODULES) {
      const res = await fetch("engine/aptg_engine/" + name);
      if (!res.ok) throw new Error("missing engine/" + name);
      py.FS.writeFile("/aptg/aptg_engine/" + name, new Uint8Array(await res.arrayBuffer()));
    }

    step("fetching the course database…", 70);
    const db = await fetch("aptg.sqlite");
    if (!db.ok) throw new Error("missing aptg.sqlite");
    py.FS.writeFile("/aptg/aptg.sqlite", new Uint8Array(await db.arrayBuffer()));

    step("building the catalogue…", 88);
    await py.runPythonAsync(`
import sys, json
sys.path.insert(0, "/aptg")
from pathlib import Path
from aptg_engine.api import State, catalogue_payload, template_courses, search_courses, plan
State.init(Path("/aptg/aptg.sqlite"))
`);

    const call = (fn, arg) => {
      py.globals.set("_arg", arg === undefined ? null : JSON.stringify(arg));
      const out = py.runPython(
        `json.dumps(${fn}(*( [json.loads(_arg)] if _arg else [] )))`
      );
      return JSON.parse(out);
    };

    // Route the page's API calls to the in-browser engine.
    const realFetch = window.fetch.bind(window);
    window.fetch = async (input, init) => {
      const url = typeof input === "string" ? input : input.url;
      if (!url.includes("/api/")) return realFetch(input, init);
      const path = url.split("/api/")[1];
      const reply = (obj) =>
        new Response(JSON.stringify(obj), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      try {
        if (path.startsWith("catalogue")) return reply(call("catalogue_payload"));
        if (path.startsWith("template")) {
          const id = new URLSearchParams(path.split("?")[1] || "").get("programme_id") || "";
          return reply({ courses: call("template_courses", id) });
        }
        if (path.startsWith("courses")) {
          const q = new URLSearchParams(path.split("?")[1] || "").get("q") || "";
          return reply({ courses: call("search_courses", q) });
        }
        if (path.startsWith("plan")) {
          const body = JSON.parse((init && init.body) || "{}");
          return reply(call("plan", body));
        }
      } catch (err) {
        return reply({ error: String(err.message || err) });
      }
      return reply({ error: "unknown endpoint" });
    };

    step("ready", 100);
    panel.remove();
    return py;
  })().catch((err) => {
    step("could not start: " + (err.message || err), 100);
    panel.querySelector(".boot-bar").style.background = "var(--warn)";
    throw err;
  });
})();
