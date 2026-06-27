import os

UI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui")

def load_ui_template() -> str:
    html_path = os.path.join(UI_DIR, "index.html")
    css_path = os.path.join(UI_DIR, "styles.css")
    js_path = os.path.join(UI_DIR, "app.js")
    
    try:
        with open(html_path, "r", encoding="utf-8") as f:
            html = f.read()
        with open(css_path, "r", encoding="utf-8") as f:
            css = f.read()
        with open(js_path, "r", encoding="utf-8") as f:
            js = f.read()
            
        # Merge assets
        html = html.replace("/* INJECT_CSS */", css)
        html = html.replace("/* INJECT_JS */", js)
        return html
    except Exception as e:
        print(f"[UI Loader Error] Failed to read files from {UI_DIR}: {e}")
        return f"<h1>UI Loader Error</h1><p>Could not read assets: {str(e)}</p>"

# Keep global variable compat for static references
HTML_TEMPLATE = load_ui_template()
