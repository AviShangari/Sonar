"""PDF reports about a run, made on request (from the assistant, in the terminal or in Teams). Plain code builds
every PDF: nothing here asks a model to write HTML or template code.

  spec.py       PdfSpec: everything a person can ask to change (sections, layout, filters, sort, look), validated;
                the named presets in config/pdf.yaml
  css.py        the check for extra styling a person asks for (no downloads, no scripts)
  data.py       what goes into a PDF: the run's report, per-portal counts, the closest rejections
  render.py     the base template (templates/run_pdf.html) to HTML, then HTML to PDF in a locked-down headless Chromium
  make.py       the one entry point: spec in, PDF file out
"""
