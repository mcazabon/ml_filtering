# Sea Shell Weekend

A small Flask project for a custom coastal-themed weekend invitation website. The starter includes:

- hero banner
- weekend itinerary
- packing list
- outfit inspiration
- location and hotel details
- RSVP/contact form
- responsive layout for mobile and desktop

## Run locally

1. Create and activate the virtual environment if needed:

   ```powershell
   .\.venv\Scripts\Activate.ps1
   ```

2. Install dependencies:

   ```powershell
   pip install -r requirements.txt
   ```

3. Start the app:

   ```powershell
   flask --app app run --debug
   ```

4. Open the local URL shown by Flask, usually `http://127.0.0.1:5000`.

## Host on IIS

This project is set up for IIS using FastCGI and `wfastcgi`.

### Publish layout

To build a clean folder you can copy to the server, run:

```powershell
.\scripts\publish-iis.ps1 -PublishRoot C:\inetpub\SeaShellSite
```

That command creates a local publish folder at `publish\iis-site\` with this layout:

```text
publish\iis-site\
  app.py
   install_host.py
  requirements.txt
  web.config
  wsgi.py
  static\
  templates\
```

The generated [web.config](web.config) inside the publish folder will already point at the target server path you passed with `-PublishRoot`.

1. On the Windows server, enable IIS with the `CGI` role service.

2. Copy the contents of `publish\iis-site\` to the server path you chose, for example:

   ```text
   C:\inetpub\SeaShellSite
   ```

3. Run the Python host installer from the deployed folder on the server:

   ```powershell
   py install_host.py
   ```

   This script creates `.venv`, upgrades `pip`, and installs everything from [requirements.txt](requirements.txt), including `Flask` and `wfastcgi`.

4. In IIS Manager, create a new site whose physical path is the project root.

5. Confirm that [web.config](web.config) points to the server's actual Python paths. The default file assumes:

   ```text
   C:\inetpub\SeaShellSite\.venv\Scripts\python.exe
   C:\inetpub\SeaShellSite\.venv\Lib\site-packages\wfastcgi.py
   ```

6. Set a real secret in `FLASK_SECRET_KEY` inside [web.config](web.config) or as a server environment variable.

7. Recycle the IIS app pool after deployment changes.

The IIS entrypoint is [wsgi.py](wsgi.py), which exposes `application` for FastCGI.

### Notes

- The publish script does not copy `.venv`; create that on the server.
- The publish folder includes [install_host.py](install_host.py) so dependency setup can be run with Python alone.
- Re-run the publish script whenever templates, static files, or app code changes.
- If you deploy to a different folder, pass that folder with `-PublishRoot` so the generated FastCGI paths stay correct.

## Customize

- Update the content data in `app.py`.
- Adjust colors, spacing, and typography in `static/css/style.css`.
- Connect the RSVP form to email, a database, or an API if you want permanent submissions.

## Filter email aliases

Before the first run, install or verify all dependencies with the preflight check:

```powershell
& .\.venv\Scripts\python.exe .\preflight.py
```

The preflight uses the active interpreter, installs missing packages from `requirements.txt`, and verifies that imports work. Run it again after changing environments or dependencies.

For the default input `C:\temp\extracted_contact_data.csv`, run:

```powershell
python alias_filter.py
```

This creates `C:\temp\extracted_contact_data_cleaned_YYYYMMDD_HHMMSS.csv`. To use a different input, pass it as the first argument:

```powershell
python alias_filter.py .\users.csv
```

The utility streams the input, so it does not load hundreds of thousands of records into memory. It splits multi-valued aliases, normalizes Active Directory prefixes such as `ADID:` and `SMTP:`, and treats `Email:` aliases by their local part. `User_ID` is retained as source data but is not used to approve an unrelated ADID. Malformed `Email:` aliases are excluded from both accepted and suspicious results and written to `alias_invalid.csv`. It combines character similarity with email-local-part, name, domain, and digit-pattern recognition, then incrementally trains a scikit-learn character n-gram model from high-confidence decisions to improve later records in the same run. A shared email domain alone is not considered a match. Every output row includes a score and reason. Lower `--threshold` to reduce false positives; raise it to review more aliases:

```powershell
python alias_filter.py .\users.csv --threshold 0.75
```
