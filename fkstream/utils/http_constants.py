# User-Agent standard utilisé partout
DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"

# Headers communs de base
DEFAULT_HEADERS = {
    "User-Agent": DEFAULT_USER_AGENT,
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate",
    "DNT": "1",
    "Connection": "keep-alive",
}

# Headers pour API JSON
JSON_HEADERS = {
    **DEFAULT_HEADERS,
    "Accept": "application/json,text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Content-Type": "application/json",
}

# Headers pour RSS/XML (Nyaa)
RSS_HEADERS = {
    **DEFAULT_HEADERS, 
    "Accept": "application/rss+xml,application/xml;q=0.9,text/html;q=0.8,*/*;q=0.7",
}

# Headers pour pages HTML (Fankai)
HTML_HEADERS = {
    **DEFAULT_HEADERS,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8", 
    "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8",
    "Upgrade-Insecure-Requests": "1",
}
