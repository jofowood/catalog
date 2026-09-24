#!/usr/bin/env python3
"""
SeaTable Static Catalog Generator
Version 2.2

Pulls artwork data and images from SeaTable, generates static HTML catalog
Images are saved with unique filenames to avoid conflicts across multiple views
"""

import requests
import json
import hashlib
import os
import re
import sys
import datetime
from pathlib import Path
from urllib.parse import urlparse, unquote, quote

__version__ = "2.2"

# Configuration - Constants (same for all catalogs)
API_TOKEN = os.environ.get("SEATABLE_API_TOKEN", "15d2c34c1ab2c226a629c1dcb9c9e02cffec1376")
SERVER_URL = "https://cloud.seatable.io"
TABLE_NAME = "Works & Exhibits"

MAIN_SITE_URL = "https://jofowo.com"

MAILING_LIST_URL = "http://eepurl.com/i9nouY"

# Nav links shown on every catalog page, alongside the "Main Site" link.
# (label, relative path)
CATALOG_NAV_PAGES = [
    ("Available", "catalog.html"),
    ("Showing", "showing.html"),
]

# Images directory (shared across all catalogs, lives at repo root)
IMAGES_DIR = Path("images")

# SeaTable column keys used for sorting
TITLE_KEY = 'gScu'
YEAR_KEY = '4UG7'

def load_config(config_file):
    """Load catalog configuration from JSON file"""
    try:
        with open(config_file, 'r') as f:
            config = json.load(f)
        
        # Validate required fields
        required = ['view_name', 'output_file', 'page_heading', 'page_title']
        # include_purchase_button is optional, defaults to False
        config['include_purchase_button'] = config.get('include_purchase_button', False)
        # sort_by_year_title is optional, defaults to False (keeps SeaTable view order)
        config['sort_by_year_title'] = config.get('sort_by_year_title', False)
        # newest_first is optional, defaults to True (only used when sorting)
        config['newest_first'] = config.get('newest_first', True)
        missing = [field for field in required if field not in config]
        if missing:
            raise ValueError(f"Missing required config fields: {', '.join(missing)}")
        
        return config
    except FileNotFoundError:
        print(f"Error: Config file '{config_file}' not found")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON in config file: {e}")
        sys.exit(1)
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)


def extract_year(value):
    """
    Pull a four-digit year out of whatever SeaTable returns
    (a number, "2024", or a date like "2024-03-01").
    Returns None if no year can be found.
    """
    if value is None or value == '':
        return None
    match = re.search(r'\d{4}', str(value))
    return int(match.group()) if match else None


def natural_key(text):
    """
    Sort key that orders numbers numerically, so "No. 2" comes before "No. 10".
    Case-insensitive, and ignores spacing around numbers so "No.09" and
    "No. 10" sort together.
    """
    parts = re.split(r'(\d+)', str(text or '').strip())
    return [(0, int(p)) if p.isdigit() else (1, p.strip().lower()) for p in parts]


def sort_rows_by_year_title(rows, newest_first=True):
    """
    Sort rows by year, then alphabetically by title.
    Works with no year always go at the end.
    """
    def key(row):
        year = extract_year(row.get(YEAR_KEY))
        if year is None:
            year_part = (1, 0)
        else:
            year_part = (0, -year if newest_first else year)
        return (year_part, natural_key(row.get(TITLE_KEY, '')))

    return sorted(rows, key=key)


def get_base_token(api_token):
    """Get temporary base token from API token"""
    response = requests.get(
        f"{SERVER_URL}/api/v2.1/dtable/app-access-token/",
        headers={"Authorization": f"Token {api_token}"}
    )
    response.raise_for_status()
    data = response.json()
    return data["access_token"], data["dtable_uuid"]


def get_metadata(base_token, base_uuid):
    """Get base metadata including tables and columns"""
    response = requests.get(
        f"{SERVER_URL}/api-gateway/api/v2/dtables/{base_uuid}/metadata/",
        headers={"Authorization": f"Bearer {base_token}"}
    )
    response.raise_for_status()
    return response.json()["metadata"]


def get_rows(base_token, base_uuid, table_name, view_name=None):
    """Get all rows from a table/view"""
    url = f"{SERVER_URL}/api-gateway/api/v2/dtables/{base_uuid}/rows/"
    params = {"table_name": table_name}
    if view_name:
        params["view_name"] = view_name
    
    response = requests.get(
        url,
        headers={"Authorization": f"Bearer {base_token}"},
        params=params
    )
    response.raise_for_status()
    return response.json()["rows"]


def get_image_filename(image_url):
    """
    Generate unique filename from image URL
    Uses hash of full path to ensure uniqueness across different uploads
    Preserves original extension
    """
    # Parse the URL to get the path
    parsed = urlparse(image_url)
    path = unquote(parsed.path)
    
    # Get original filename and extension
    original_filename = Path(path).name
    extension = Path(original_filename).suffix
    
    # Create hash of full path (includes UUID, date, etc)
    # This ensures same image = same filename, different images = different filenames
    path_hash = hashlib.md5(path.encode()).hexdigest()[:12]
    
    # Return: hash + extension (e.g., "a1b2c3d4e5f6.jpg")
    return f"{path_hash}{extension}"


def download_image(image_url, api_token, output_path):
    """Download image from SeaTable to output path"""
    # Skip if already exists
    if output_path.exists():
        print(f"  ✓ Already exists: {output_path.name}")
        return True
    
    # Extract path from URL - need everything after /asset/{uuid}/
    # URL format: https://cloud.seatable.io/workspace/XX/asset/{uuid}/images/2024-02/file.jpg
    parsed = urlparse(image_url)
    path = unquote(parsed.path)
    
    # Split on /asset/ and get everything after the UUID
    if '/asset/' in path:
        after_asset = path.split('/asset/')[1]
        # Remove the UUID part (first segment after /asset/)
        path_parts = after_asset.split('/', 1)
        if len(path_parts) > 1:
            relative_path = path_parts[1]  # e.g., "images/2024-02/file.jpg"
        else:
            print(f"  ✗ Could not parse path: {path}")
            return False
    else:
        print(f"  ✗ Invalid URL format: {image_url}")
        return False
    
    # Get download link
    response = requests.get(
        f"{SERVER_URL}/api/v2.1/dtable/app-download-link/",
        headers={"Authorization": f"Token {api_token}"},
        params={"path": relative_path}
    )
    response.raise_for_status()
    download_url = response.json()["download_link"]
    
    # Download the file
    response = requests.get(download_url, stream=True)
    response.raise_for_status()
    
    with open(output_path, 'wb') as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)
    
    print(f"  ✓ Downloaded: {output_path.name}")
    return True


def find_image_column(columns):
    """Find the first image column in the table"""
    # First, try to find by column key (from the HTML we know it's 'Jcpv')
    for col in columns:
        if col.get("key") == "Jcpv":
            return col.get("key")
    
    # Fallback: find any image column
    for col in columns:
        if col.get("type") == "image":
            return col.get("key") or col.get("name")
    
    return None


def generate_html(rows, image_column, columns, page_heading, page_title, config):
    """Generate HTML catalog page matching existing style"""
    
    # Find specific columns by name
    column_map = {col['name']: col for col in columns}
    
    # Build the shared nav: a link back to the main site, plus a link to
    # each catalog page (including the current one -- same nav everywhere).
    nav_links = [f'<a href="{MAIN_SITE_URL}">Main Site</a>']
    for label, href in CATALOG_NAV_PAGES:
        nav_links.append(f'<a href="{href}">{label}</a>')
    nav_links_html = "\n                ".join(nav_links)
    
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{page_title}</title>
    <!-- Required in this exact order: Adobe Fonts embed, then the shared
         house stylesheet. -->
    <link rel="stylesheet" href="https://use.typekit.net/fpc1twk.css">
    <link rel="stylesheet" href="https://jofowood.github.io/shared/jofowo-github.css">
    <style>
        /* Page-specific layout only -- colors, type, buttons, and the site
           header/footer come from the shared stylesheet. This block just
           handles the catalog grid and artwork card internals. */

        .wrap > h1 {{
            text-align: center;
        }}

        .note {{
            text-align: center;
            margin-bottom: 30px;
        }}

        .catalog-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
            gap: 30px;
            justify-items: center;
        }}

        @media (max-width: 640px) {{
            .catalog-grid {{
                grid-template-columns: 1fr;
                max-width: 400px;
                margin: 0 auto;
            }}
        }}

        .artwork-card {{
            width: 100%;
            background: var(--panel);
            border: 1px solid var(--border);
            border-radius: 5px;
            overflow: hidden;
        }}

        .artwork-image {{
            width: 100%;
            min-height: 300px;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }}

        .artwork-image img {{
            max-width: 100%;
            max-height: 300px;
            width: auto;
            height: auto;
            object-fit: contain;
            display: block;
        }}

        .artwork-info {{
            padding: 20px;
        }}

        .artwork-title {{
            font-size: 1.1rem;
            font-weight: 600;
            margin-bottom: 8px;
            color: var(--ink);
        }}

        .artwork-meta {{
            font-size: 0.9rem;
            color: var(--ink-soft);
            line-height: 1.6;
        }}

        .artwork-meta div {{
            margin-bottom: 4px;
        }}

        .inv-number {{
            font-family: var(--font);
            color: var(--ink-soft);
            font-size: 0.85rem;
            margin-bottom: 8px;
        }}

        .price {{
            margin-top: 8px;
            font-weight: 600;
            color: var(--ink);
        }}

        .artwork-info .btn {{
            display: block;
            width: 100%;
            margin-top: 30px;
        }}
    </style>
</head>
<body>
    <div class="wrap">
        <header class="site-header">
            <a class="site-logo-link" href="{MAIN_SITE_URL}">John Woodruff</a>
            <nav class="site-nav">
                {nav_links_html}
            </nav>
        </header>
        <h1>{page_heading}</h1>
        <p class="note">All Images &copy; John Woodruff 2020-{datetime.datetime.now().year}</p>
        <div class="catalog-grid">
        
"""

    
    for row in rows:
        # Get images
        images = row.get(image_column, [])
        if not images:
            continue
        
        # Use first image
        image_url = images[0] if isinstance(images, list) else images
        image_filename = get_image_filename(image_url)
        
        # Extract fields using actual column keys from SeaTable
        inventory = row.get('0000', '')  # Inventory number
        title = row.get('gScu', 'Untitled')  # Title
        year = row.get('4UG7', '')  # Date/Year
        edition = row.get('rXGj', '')  # Current sequence/edition
        image_size = row.get('gWXH', '')  # Image size
        paper_size = row.get('2Te2', '')  # Paper size
        frame_size = row.get('6Ci3', '')  # Frame size
        edition_desc = row.get('3y0u', '')  # Edition description
        medium = row.get('Xe9e', '')  # Medium
        price = row.get('upE4', '')  # Price
        
        # Build card HTML
        html += f"""            <div class="artwork-card">
                <div class="artwork-image">
                    <img src="images/{image_filename}" alt="{title}">
                </div>
                <div class="artwork-info">
                    <div class="artwork-title">{title}</div>
                    <div class="artwork-meta">
"""
        
        if inventory:
            html += f"""                        <div class="inv-number">{inventory}</div>\n"""
        if year:
            html += f"""                        <div><strong>Year:</strong> {year}</div>\n"""
        if edition:
            html += f"""                        <div><strong>Edition:</strong> {edition}</div>\n"""
        if image_size:
            html += f"""                        <div><strong>Image:</strong> {image_size}"</div>\n"""
        if paper_size:
            html += f"""                        <div><strong>Paper:</strong> {paper_size}"</div>\n"""
        if frame_size:
            html += f"""                        <div><strong>Frame:</strong> {frame_size}"</div>\n"""
        if edition_desc:
            html += f"""                        <div style="margin-top: 10px; font-size: 0.85rem; line-height: 1.5;">{edition_desc}</div>\n"""
        if medium:
            html += f"""                        <div><strong>Medium:</strong> {medium}</div>\n"""
        if price:
            html += f"""                        <div class="price">${price}</div>\n"""
        
        # Build email inquiry link with proper formatting
        email_body = "I'm interested in the following artwork:\n\n"
        email_body += f"Title: {title}\n"
        if inventory:
            email_body += f"Inventory: {inventory}\n"
        if year:
            email_body += f"Year: {year}\n"
        if edition:
            email_body += f"Edition: {edition}\n"
        if image_size:
            email_body += f'Image Size: {image_size}"\n'
        if paper_size:
            email_body += f'Paper Size: {paper_size}"\n'
        if frame_size:
            email_body += f'Frame Size: {frame_size}"\n'
        if edition_desc:
            email_body += f"\nDetails: {edition_desc}\n"
        if medium:
            email_body += f"Medium: {medium}\n"
        if price:
            email_body += f"\nPrice: ${price}\n"
        
        email_subject = f"Inquiry: {title}"
        if inventory:
            email_subject += f" ({inventory})"
        
        # URL encode for mailto link
        mailto_link = f"mailto:jofowood@gmail.com?subject={quote(email_subject)}&body={quote(email_body)}"
        
        html += f"""                        <a href="{mailto_link}" class="btn">Inquire</a>\n"""
        
        # Only add Purchase Info button if configured
        if config.get('include_purchase_button', False):
            # Build Google Form purchase info link
            form_base_url = "https://docs.google.com/forms/d/e/1FAIpQLSeexuq8vTsj5KrOr4trdD1vFrIVnS31sMlGT8sQB_Egc3Idag/viewform"
            github_base_url = "https://jofowood.github.io/catalog"
            image_url_full = f"{github_base_url}/images/{image_filename}"
            
            purchase_link = f"{form_base_url}?entry.370646706={quote(title)}&entry.673557102={quote(image_url_full)}"

            html += f"""                        <a href="{purchase_link}" class="btn primary" target="_blank">Purchase</a>\n"""

        html += """                    </div>
                </div>
            </div>
"""

    html += f"""        </div>
        <footer class="site-footer">
            <a class="back-to-top" href="#">↑ Back to Top</a>
            <p class="copyright-line">Copyright &copy; 2020&ndash;{datetime.datetime.now().year} John Woodruff | <a href="{MAILING_LIST_URL}">Mailing List</a></p>
        </footer>
    </div>
</body>
</html>"""

    return html


def main():
    # Check for config file argument
    if len(sys.argv) < 2:
        print("Usage: python3 generate_catalog.py <config_file.json>")
        print("\nExample: python3 generate_catalog.py config_available_works.json")
        sys.exit(1)
    
    config_file = sys.argv[1]
    
    # Load configuration
    config = load_config(config_file)
    view_name = config['view_name']
    output_file = Path(config['output_file'])
    page_heading = config['page_heading']
    page_title = config['page_title']
    
    print(f"SeaTable Static Catalog Generator v{__version__}")
    print("=" * 50)
    print(f"Config: {config_file}")
    print(f"View: {view_name}")
    print(f"Output: {output_file}")
    
    # Create output directories
    output_file.parent.mkdir(parents=True, exist_ok=True)
    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    
    # Get base token
    print("\n1. Authenticating with SeaTable...")
    base_token, base_uuid = get_base_token(API_TOKEN)
    print(f"   ✓ Connected to base: {base_uuid[:8]}...")
    
    # Get metadata
    print("\n2. Loading base structure...")
    metadata = get_metadata(base_token, base_uuid)
    tables = metadata["tables"]
    
    # Select table
    table = tables[0] if not TABLE_NAME else next(t for t in tables if t["name"] == TABLE_NAME)
    print(f"   ✓ Using table: {table['name']}")
    
    # Find image column
    image_column = find_image_column(table["columns"])
    if not image_column:
        print("   ✗ No image column found!")
        return
    print(f"   ✓ Image column: {image_column}")
    
    # Get all columns for display
    all_columns = table["columns"]
    
    # Get rows
    print(f"\n3. Loading rows from view: {view_name}...")
    rows = get_rows(base_token, base_uuid, table["name"], view_name)
    print(f"   ✓ Found {len(rows)} rows")
    
    # Optionally sort by year, then title
    if config['sort_by_year_title']:
        rows = sort_rows_by_year_title(rows, newest_first=config['newest_first'])
        direction = "newest first" if config['newest_first'] else "oldest first"
        print(f"   ✓ Sorted by year ({direction}), then title")
        for row in rows:
            print(f"      {extract_year(row.get(YEAR_KEY))} | {row.get(YEAR_KEY)!r} | {row.get(TITLE_KEY, '')}")
    else:
        print("   - Sorting OFF (sort_by_year_title not set in config) -- using SeaTable view order")
    
    # Download images
    print(f"\n4. Downloading images to {IMAGES_DIR}...")
    image_count = 0
    for i, row in enumerate(rows, 1):
        images = row.get(image_column, [])
        if not images:
            continue
        
        # Get first image
        image_url = images[0] if isinstance(images, list) else images
        image_filename = get_image_filename(image_url)
        output_path = IMAGES_DIR / image_filename
        
        print(f"   [{i}/{len(rows)}] {row.get('gScu', row.get('Title', row.get('Name', 'Untitled')))}")
        download_image(image_url, API_TOKEN, output_path)
        image_count += 1
    
    print(f"\n   ✓ Processed {image_count} images")
    
    # Generate HTML
    print(f"\n5. Generating {output_file}...")
    html = generate_html(rows, image_column, all_columns, page_heading, page_title, config)
    output_file.write_text(html, encoding="utf-8")
    print(f"   ✓ Catalog generated!")
    
    print(f"\n✓ Complete!")
    print(f"\nNext steps:")
    print(f"  1. Review the generated catalog: {output_file}")
    print(f"  2. Commit to git: git add {output_file} images/")
    print(f"  3. Push to GitHub: git push")


if __name__ == "__main__":
    main()
