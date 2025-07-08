import re

# Multiplicateurs de taille standardisés
SIZE_UNITS = {
    'b': 1, 'byte': 1, 'bytes': 1,
    'kib': 1024, 'kb': 1000,
    'mib': 1024**2, 'mb': 1000**2,
    'gib': 1024**3, 'gb': 1000**3, 
    'tib': 1024**4, 'tb': 1000**4,
}


def parse_size_to_bytes(size_str: str) -> int:
    """
    Convertit une chaîne de taille en bytes
    Remplace les fonctions _parse_size dupliquées dans nyaa.py et nyaa_matcher.py
    
    Exemples: "1.5 GB" → 1500000000, "250 MiB" → 262144000
    """
    if not size_str or size_str.strip() == "":
        return 0
        
    try:
        size_str = size_str.lower().strip().replace(',', '')
        parts = size_str.split()
        
        if len(parts) == 1:
            # Format "5GB" sans espace
            match = re.match(r'([0-9.]+)([a-z]+)', size_str)
            if match:
                num_str, unit = match.groups()
            else:
                return 0
        elif len(parts) == 2:
            # Format "5 GB" avec espace
            num_str, unit = parts
        else:
            return 0
        
        num = float(num_str)
        unit = unit.lower()
        
        if unit in SIZE_UNITS:
            return int(num * SIZE_UNITS[unit])
        else:
            return 0
            
    except (ValueError, IndexError, AttributeError):
        return 0


def bytes_to_size(bytes_count: int) -> str:
    """
    Convertit des bytes en format lisible
    Compatible avec la fonction existante dans general.py
    """
    sizes = ["Bytes", "KB", "MB", "GB", "TB"]
    if bytes_count == 0:
        return "0 Byte"

    i = 0
    while bytes_count >= 1024 and i < len(sizes) - 1:
        bytes_count /= 1024
        i += 1

    return f"{round(bytes_count, 2)} {sizes[i]}"
