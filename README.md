# Rainy

**A local job-search and application assistant for Windows.** It scores hundreds of postings against your CV and fills
the employer's own application form in a real browser — you approve every batch before anything is sent.

[![tests](https://github.com/rainykarthick-crypto/rainy/actions/workflows/tests.yml/badge.svg)](https://github.com/rainykarthick-crypto/rainy/actions/workflows/tests.yml)
[![release](https://img.shields.io/github/v/release/rainykarthick-crypto/rainy?color=2f81f7)](https://github.com/rainykarthick-crypto/rainy/releases/latest)
[![license](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![platform](https://img.shields.io/badge/platform-Windows%2010%2F11%20x64-0a7bbb)]()

![The Rainy dashboard: 664 scored jobs, filtered by score, with the batch apply controls](docs/dashboard.png)

Rainy is a **remake of [AIHawk](https://github.com/feder-cr/Jobs_Applier_AI_Agent)** (MIT). AIHawk built the hard parts —
the job database, the browser automation, the form-filling model. This fork puts a dashboard on top, opens it to any
OpenAI-compatible model, encrypts the API key, and fixes the places where the browser used to stall.

---

## Download

| File | Size | Start | Get it |
|---|---|---|---|
| **Rainy.exe** | 98 MB | ~20 s | [single file, nothing else needed](https://github.com/rainykarthick-crypto/rainy/releases/latest/download/Rainy.exe) |
| **Rainy-1.0.1-win64.zip** | 98 MB | ~9 s | [extract, run `Rainy\Rainy.exe`](https://github.com/rainykarthick-crypto/rainy/releases/download/v1.0.1/Rainy-1.0.1-win64.zip) |

Windows 10/11, x64. Unsigned, so SmartScreen warns once: **More info → Run anyway**.

```powershell
Rainy.exe              # dashboard, opens in your browser
Rainy.exe --tui        # the terminal interface
Rainy.exe --install    # copy to %LOCALAPPDATA%\Programs\Rainy and add shortcuts
```

---

## Up and running in five steps

| | |
|---|---|
| **1. Start Rainy** | The dashboard opens at `http://127.0.0.1:8765` |
| **2. Paste an API key** | Checked against the gateway, then sealed with Windows DPAPI — only your Windows account can read it |
| **3. Upload your CV** | The search plan — titles, places, weights — is built from it |
| **4. Fill in your details** | Salary, notice period, address, education, work history: what every form asks for |
| **5. Fetch new jobs** | Then select and apply. Tick **trial** first to fill forms without sending |

### Two people, one computer

Each account is a folder, and each folder is its own search — its own CV, answers, jobs, application history and browser
sessions. One shortcut per person starts each dashboard beside the other's:

```powershell
Rainy.exe --home "%USERPROFILE%\.aihawk"            --port 8765   # the default account
Rainy.exe --home "%USERPROFILE%\.rainy-kavipriya"   --port 8766   # a second account
```

The header shows `account: .rainy-kavipriya` so you can tell them apart at a glance. Without `--home`, Rainy uses
`%USERPROFILE%\.aihawk`, or the folder named by `AIHAWK_HOME`.

---

## What it does

| Feature | |
|---|---|
| **Scored job list** | Every posting ranked 1–5 against your CV, filtered by score, status and text |
| **Batch apply** | Select a page, apply, watch each step live, stop between applications |
| **Trial mode** | Fills every required field and stops at the send button |
| **Resume upload** | PDF, Word or text; the search is rebuilt from your CV |
| **Editable answers** | Every detail the forms receive, changed in place, used by the next application |
| **Encrypted key** | Validated before saving, sealed with DPAPI, never displayed |
| **Chat** | Ask about your search; it answers from your live jobs and answers |
| **Tailored CV** | Each posting gets the CV rewritten in its own words — reordered and re-emphasised, never a fact added — sent as a PDF under your own file's name. `auto` does it only where the model judges it helps |
| **Cost dial** | Page answers Economy / Balanced / Quality: the smaller, the fewer tokens an application spends |
| **Experience** | Your years, from fresher to 10+: a posting asking for much more scores lower |
| **Add by link** | Paste a job URL — read, scored, added to the list. A portal that blocks a plain request is opened in the real browser instead |
| **Fetch new jobs** | Sync the database, search again, score what is new |
| **Updates** | The Version panel checks GitHub and installs the newest release, restarting Rainy with the same account and port |

## How it works

1. **Search.** A database of postings from 37 public sources, built by AIHawk's pipeline, is downloaded to your
   computer and searched locally — nothing about your search leaves the machine.
2. **Score.** A model reads each posting against your CV and says how sure it is that the job fits. That certainty is
   the score.
3. **Fill.** A model drives a real Firefox on the employer's form and fills every required field from your CV and
   answers, including the CV upload and voluntary disclosures. The form a site showed is kept after the first run
   there — every field, its selector and the send control — so the next job on that host starts from that map instead of
   reading the whole page again. On a Greenhouse form that measured **25% fewer tokens and half the time**; long page
   readings are also cut to their first 8,000 characters, with a note saying how to read the rest.
4. **You send.** Nothing is submitted until you select the jobs and confirm the batch. Each outcome is recorded with a
   screenshot of what the page said.

## Configuration

Settings live in `%USERPROFILE%\.aihawk\aihawk.yaml`; see [configuration](docs/configuration.md). The gateway and models:

```yaml
base_url: https://api.deepseek.com/v1   # any OpenAI-compatible gateway
model: deepseek-flash                   # fills the forms; reads screenshots
quick_model: deepseek-flash             # short calls: the search plan
decision_model: deepseek-flash          # scores the postings
max_age_days: 30
```

Your CV, answers and application history stay in `%USERPROFILE%\.aihawk` on your computer. What leaves it is only what
the model is asked: your CV, your answers, and the text of the pages it fills.

## Known limits

- Windows x64 for the packaged builds — the browser engine has no Mac version
- LinkedIn postings are excluded from the database by design; paste the company's own application link instead
- A form that demands a profile photo cannot be completed — only the CV is uploaded
- The browser engine (~740 MB) downloads once, on the first application
- A captcha that is not a simple checkbox stops the form, and you finish that one by hand

## Development

```powershell
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
.venv\Scripts\python -m pytest -m "not browser"    # 552 tests
.venv\Scripts\python dashboard.py                  # dashboard from source
```

`launcher.py` is what the packaged builds run: the dashboard by default, `--tui` for the terminal app, and
`--invisible-mcp` for the browser server it starts for itself when frozen into one file.

## Licence and credit

MIT, as AIHawk was. The original copyright notices stay in [LICENSE](LICENSE) and [NOTICE](NOTICE). Issues about the
job database or the browser engine belong upstream at
[feder-cr/Jobs_Applier_AI_Agent](https://github.com/feder-cr/Jobs_Applier_AI_Agent).
