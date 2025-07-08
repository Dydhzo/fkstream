FROM ghcr.io/astral-sh/uv:python3.11-alpine
LABEL name="FKStream" \
      description="FKStream – Addon non officiel pour accéder au contenu de Fankai" \
      url="https://github.com/Dydhzo/fkstream"

WORKDIR /app

# ✅ BUILDS REPRODUCTIBLES - Copier les fichiers de dépendances
COPY pyproject.toml uv.lock* ./

# ✅ INSTALLATION SÉCURISÉE - Utiliser les versions exactes ou générer un lock
RUN if [ -f uv.lock ]; then \
        echo "📦 Utilisation de uv.lock existant pour une construction reproductible"; \
        uv sync --frozen; \
    else \
        echo "⚠️ Aucun uv.lock trouvé, installation depuis pyproject.toml"; \
        uv sync; \
    fi

COPY . .

ENTRYPOINT ["uv", "run", "python", "-m", "fkstream.main"]
