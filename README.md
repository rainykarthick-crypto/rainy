# Rainy

A local job-search and application assistant for Windows.

Rainy searches a published database of postings, scores them against your CV with an AI model, and fills the company's
own application form in a real browser. Nothing is sent until you select the jobs and confirm the batch.

Rainy is a **remake of [AIHawk](https://github.com/feder-cr/Jobs_Applier_AI_Agent)** (MIT). AIHawk did the hard part —
the job database, the browser automation, the form-filling model. This fork adds a local dashboard, support for any
OpenAI-compatible gateway, an encrypted key, and fixes for the places where the browser stalled.

## Download

| File | Size | Start | Notes |
|---|---|---|---|
| [`Rainy.exe`](https://github.com/rainykarthick-crypto/rainy/releases/latest/download/Rainy.exe) | 87 MB | ~20 s | one file, nothing else needed |
| [`Rainy-1.0.0-win64.zip`](https://github.com/rainykarthick-crypto/rainy/releases/download/v1.0.0/Rainy-1.0.0-win64.zip) | 87 MB | ~9 s | extract, then run `Rainy\Rainy.exe` |

Windows 10/11, x64. The binaries are unsigned, so SmartScreen warns once: **More info → Run anyway**.

```powershell
Rainy.exe              # dashboard, opens in your browser
Rainy.exe --tui        # the terminal interface
Rainy.exe --install    # copy to %LOCALAPPDATA%\Programs\Rainy and add shortcuts
```

## First run

1. **Start Rainy.** The dashboard opens at `http://127.0.0.1:8765`.
2. **Paste an API key.** It is checked against the gateway and then sealed with Windows DPAPI — only your Windows
   account on this computer can read it.
3. **Upload your CV.** The search plan — titles, places and their weights — is built from it.
4. **Fill in your details** in the answers panel: salary, notice period, address, education, work history.
5. **Fetch new jobs**, then select and apply.

Tick **trial** before applying to fill every form and send nothing. That is the safe way to see what the model does on a
site you have not applied to before.

## What the dashboard does

| | |
|---|---|
| **Jobs** | Every scored posting, filtered by score, status and text, with links to the real listing |
| **Apply** | Select shown, apply in a batch, live progress, a log, and Stop between applications |
| **Trial** | Fill every form, send none |
| **Resume** | Upload a PDF, Word or text file; the search plan is rebuilt from it |
| **Details** | Edit any answer the forms receive, add new ones, remove them |
| **API key** | Validated before saving, encrypted at rest, never displayed |
| **Chat** | Ask about your search; it answers with your live job and answer context |
| **Add job** | Paste any company job link — read, scored and added to the list |
| **Fetch new jobs** | Bring the database up to date, search, and score what is new |

## How it works

1. **Search.** A database of postings from 37 public sources, rebuilt by AIHawk's pipeline, is downloaded to your
   computer and searched locally.
2. **Score.** A model reads each posting against your CV and answers how sure it is that the job fits. That certainty is
   the score, 1 to 5.
3. **Fill.** A model drives a real Firefox on the employer's form, filling every required field from your CV and answers.
4. **You send.** The application is submitted only after you select the jobs and confirm. Each outcome is recorded with
   a screenshot of what the page said.

## Configuration

Settings live in `%USERPROFILE%\.aihawk\aihawk.yaml`, changed with `e` in the terminal app or by editing the file. See
[configuration](docs/configuration.md). The models and the gateway are here:

```yaml
base_url: https://api.deepseek.com/v1   # any OpenAI-compatible gateway
model: deepseek-flash                   # fills the forms (reads screenshots)
quick_model: deepseek-flash             # short calls: the search plan
decision_model: deepseek-flash          # scores the postings
max_age_days: 30
```

Answers, the CV and what was sent stay in `%USERPROFILE%\.aihawk` on your computer. The only things that leave it are
what the model is asked: your CV, the answers, and the text of the pages it fills.

## Known limits

- Windows x64 only for the packaged builds; the engine has no Mac version
- LinkedIn postings are excluded from the database by design — paste the company's own application link instead
- A form that requires a profile photo cannot be completed; only the CV is uploaded
- The browser engine (~740 MB) downloads once, on the first application
- A captcha that is not a simple box stops the form, and you finish it by hand

## Development

```powershell
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
.venv\Scripts\python -m pytest -m "not browser"    # unit tests
.venv\Scripts\python dashboard.py                  # the dashboard from source
.venv\Scripts\python -m PyInstaller --onedir ...   # see launcher.py
```

`launcher.py` is the entry point the packaged builds use: the dashboard by default, `--tui` for the terminal app,
`--invisible-mcp` for the browser server it starts for itself.

## Licence and credit

MIT, as AIHawk was. The original copyright notices are kept in [LICENSE](LICENSE) and [NOTICE](NOTICE); this fork's
changes are described in the commit history. Issue reports about the job database or the browser engine belong upstream
at [feder-cr/Jobs_Applier_AI_Agent](https://github.com/feder-cr/Jobs_Applier_AI_Agent).
