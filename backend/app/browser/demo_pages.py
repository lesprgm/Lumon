from __future__ import annotations


def primary_demo_html(task_text: str) -> str:
    return f"""
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Lumon Demo Travel Search</title>
    <style>
      :root {{
        font-family: Inter, system-ui, sans-serif;
        color: #f7fafc;
        background: #07111b;
      }}
      * {{
        box-sizing: border-box;
      }}
      body {{
        margin: 0;
        min-height: 100vh;
        background:
          radial-gradient(circle at top left, rgba(48, 140, 255, 0.28), transparent 32%),
          radial-gradient(circle at top right, rgba(247, 177, 52, 0.2), transparent 28%),
          linear-gradient(180deg, #0c1722 0%, #08111b 100%);
      }}
      main {{
        width: min(1100px, calc(100vw - 96px));
        margin: 48px auto;
        display: grid;
        grid-template-columns: 1.2fr 0.8fr;
        gap: 24px;
      }}
      .hero, .results {{
        background: rgba(10, 18, 28, 0.84);
        border: 1px solid rgba(255, 255, 255, 0.08);
        border-radius: 28px;
        padding: 28px;
        box-shadow: 0 24px 80px rgba(0, 0, 0, 0.32);
      }}
      h1 {{
        margin: 0 0 8px;
        font-size: 2.4rem;
      }}
      p {{
        color: #c4d1df;
      }}
      .task-pill {{
        display: inline-flex;
        padding: 6px 12px;
        border-radius: 999px;
        background: rgba(77, 171, 247, 0.16);
        color: #9dd8ff;
        margin-bottom: 18px;
      }}
      .field-grid {{
        display: grid;
        grid-template-columns: 1fr 1fr;
        gap: 14px;
        margin-top: 28px;
      }}
      label {{
        display: flex;
        flex-direction: column;
        gap: 8px;
        font-size: 0.95rem;
        color: #dce7f3;
      }}
      input, select, button {{
        border-radius: 16px;
        border: none;
        padding: 15px 16px;
        font: inherit;
      }}
      input, select {{
        background: rgba(255, 255, 255, 0.08);
        color: #f7fafc;
      }}
      .search-row {{
        margin-top: 18px;
        display: flex;
        gap: 14px;
      }}
      #search-button, #shortlist-button {{
        background: linear-gradient(135deg, #56b3ff, #2476ff);
        color: white;
        font-weight: 700;
        cursor: pointer;
      }}
      #search-button {{
        min-width: 180px;
      }}
      #results-list {{
        display: grid;
        gap: 12px;
        margin-top: 18px;
      }}
      .hotel-card {{
        border-radius: 20px;
        padding: 18px;
        background: rgba(255, 255, 255, 0.05);
        border: 1px solid rgba(255, 255, 255, 0.08);
      }}
      .hotel-card strong {{
        display: block;
        font-size: 1.05rem;
      }}
      .hotel-card span {{
        color: #c6d4e2;
      }}
      .hidden {{
        display: none !important;
      }}
      .notice {{
        margin-top: 20px;
        padding: 14px 16px;
        border-radius: 18px;
        background: rgba(255, 183, 3, 0.14);
        color: #ffe7ab;
      }}
    </style>
  </head>
  <body>
    <main>
      <section class="hero">
        <div class="task-pill">Lumon primary demo flow</div>
        <h1>Find a hotel under budget</h1>
        <p id="task-copy">{task_text}</p>
        <div class="field-grid">
          <label>
            Destination
            <input id="destination" value="" placeholder="Where are you going?" />
          </label>
          <label>
            Dates
            <input id="dates" value="" placeholder="Apr 18 - Apr 20" />
          </label>
          <label>
            Max price
            <select id="budget">
              <option value="250">$250</option>
              <option value="300">$300</option>
              <option value="400">$400</option>
            </select>
          </label>
          <label>
            Rating
            <select id="rating">
              <option value="8+">8.0+</option>
              <option value="7+">7.0+</option>
            </select>
          </label>
        </div>
        <div class="search-row">
          <button id="search-button" type="button">Search hotels</button>
          <div id="search-status" class="notice hidden">Results loaded. Review before shortlisting.</div>
        </div>
      </section>
      <aside class="results">
        <h2>Shortlist</h2>
        <p>Three deterministic listings appear after search. The shortlist step is the approval checkpoint.</p>
        <div id="results-list"></div>
        <button id="shortlist-button" type="button" class="hidden">Create shortlist</button>
        <div id="shortlist-status" class="notice hidden">Shortlist created successfully.</div>
      </aside>
    </main>
    <script>
      const searchButton = document.getElementById("search-button");
      const searchStatus = document.getElementById("search-status");
      const resultsList = document.getElementById("results-list");
      const shortlistButton = document.getElementById("shortlist-button");
      const shortlistStatus = document.getElementById("shortlist-status");
      const listings = [
        {{ name: "Lattice Hotel", price: "$219", score: "8.8" }},
        {{ name: "Mercer Stay", price: "$239", score: "8.5" }},
        {{ name: "Harbor Thread", price: "$247", score: "8.2" }}
      ];

      searchButton.addEventListener("click", () => {{
        resultsList.innerHTML = "";
        listings.forEach((listing) => {{
          const card = document.createElement("article");
          card.className = "hotel-card";
          card.innerHTML = `<strong>${{listing.name}}</strong><span>${{listing.price}} • Rating ${{listing.score}}</span>`;
          resultsList.appendChild(card);
        }});
        searchStatus.classList.remove("hidden");
        shortlistButton.classList.remove("hidden");
      }});

      shortlistButton.addEventListener("click", () => {{
        shortlistStatus.classList.remove("hidden");
      }});
    </script>
  </body>
</html>
"""


def backup_demo_html(task_text: str) -> str:
    return f"""
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Lumon Backup Demo</title>
    <style>
      body {{
        margin: 0;
        font-family: "IBM Plex Sans", system-ui, sans-serif;
        background: linear-gradient(180deg, #1f2937 0%, #0f172a 100%);
        color: #f8fafc;
      }}
      .layout {{
        width: min(1080px, calc(100vw - 72px));
        margin: 32px auto;
        display: grid;
        grid-template-columns: 2fr 1fr;
        gap: 18px;
      }}
      .card {{
        background: rgba(15, 23, 42, 0.88);
        border-radius: 24px;
        padding: 24px;
        border: 1px solid rgba(255, 255, 255, 0.08);
      }}
      button, input {{
        font: inherit;
        border-radius: 14px;
        border: none;
        padding: 14px 16px;
      }}
      button {{
        background: #22c55e;
        color: #08111b;
        font-weight: 700;
      }}
      .row {{
        display: grid;
        gap: 12px;
      }}
      .listing {{
        margin-top: 14px;
        padding: 14px;
        border-radius: 16px;
        background: rgba(255, 255, 255, 0.05);
      }}
      .hidden {{
        display: none;
      }}
    </style>
  </head>
  <body>
    <div class="layout">
      <section class="card row">
        <div>Fallback script</div>
        <h1>Backup hotel search</h1>
        <p>{task_text}</p>
        <input id="backup-destination" placeholder="Destination" />
        <input id="backup-dates" placeholder="Dates" />
        <button id="backup-search" type="button">Run backup search</button>
      </section>
      <aside class="card">
        <h2>Backup shortlist</h2>
        <div id="backup-results"></div>
        <button id="backup-shortlist" type="button" class="hidden">Approve shortlist</button>
        <div id="backup-complete" class="listing hidden">Backup shortlist complete.</div>
      </aside>
    </div>
    <script>
      const results = document.getElementById("backup-results");
      const shortlist = document.getElementById("backup-shortlist");
      const complete = document.getElementById("backup-complete");
      document.getElementById("backup-search").addEventListener("click", () => {{
        results.innerHTML = `
          <div class="listing"><strong>Fallback Stay</strong><div>$229 • Rating 8.4</div></div>
          <div class="listing"><strong>Signal Hotel</strong><div>$245 • Rating 8.1</div></div>
        `;
        shortlist.classList.remove("hidden");
      }});
      shortlist.addEventListener("click", () => {{
        complete.classList.remove("hidden");
      }});
    </script>
  </body>
</html>
"""
