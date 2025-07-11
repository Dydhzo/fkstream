<div align="center">
  <img src="https://raw.githubusercontent.com/Dydhzo/fkstream/refs/heads/main/fkstream/assets/fkstream-logo.jpg" alt="FKStream Logo" width="150">
  <h1>FKStream</h1>
  <p><strong>Addon Stremio non officiel pour accéder au contenu de Fankai.</strong></p>
  <p>
    <img src="https://img.shields.io/badge/status-fonctionnel-success?style=for-the-badge" alt="Status">
    <img src="https://img.shields.io/badge/python-3.11+-blue?style=for-the-badge&logo=python" alt="Python Version">
  </p>
</div>

---

## 🌟 À propos

**FKStream** est un addon non officiel pour Stremio, conçu pour accéder au contenu proposé par Fankai. Il permet de parcourir une large bibliothèque d’animes, avec une prise en charge avancée des services de débridage pour un streaming fluide et optimisé.

> **Hébergé par Fankai** : Une instance est disponible à l'adresse https://streamio.fankai.fr/configure

## ✨ Fonctionnalités

- **Catalogue Fankai Complet** : Accès à l'ensemble des animes disponibles sur Fankai.
- **Intégration Debrid** : Supporte de nombreux services de débridage (Real-Debrid, AllDebrid, Premiumize, etc.) pour un streaming haute vitesse.
- **Mode Torrent Direct** : Possibilité d'envoyer les torrents directement à Stremio sans passer par un service debrid.
- **Mise en Cache Intelligente** : Cache les métadonnées et la disponibilité des liens pour des chargements plus rapides.
- **Matching Précis** : Algorithme avancé pour trouver le bon fichier vidéo correspondant à un épisode, même dans des packs contenant toute une saison.
- **Interface de Configuration Web** : Une page de configuration simple et claire pour paramétrer l'addon facilement.
- **Support Proxy** : Permet de router les requêtes via un proxy pour plus de flexibilité.
- **Déploiement Facile** : Prêt à être déployé avec Docker pour un hébergement simple et rapide.

## 🚀 Installation

Il y a deux méthodes principales pour installer et utiliser cet addon : en local ou via Docker.

### Méthode 1 : Installation Locale (Simple)

Idéal pour une utilisation sur votre machine personnelle.

1.  **Prérequis** : Assurez-vous d'avoir [Python 3.11](https://www.python.org/downloads/) ou une version plus récente installé.
2.  **Clonez le projet** :
    ```bash
    git clone https://github.com/Dydhzo/fkstream.git
    cd fkstream
    ```
3.  **Créez un environnement virtuel et installez les dépendances** :
    ```bash
    python3 -m venv .venv
    source .venv/bin/activate  # Sur Windows, utilisez: .venv\Scripts\activate
    pip install -e .
    ```
4.  **Lancez l'application** :
    ```bash
    python -m fkstream.main
    ```
5.  L'addon est maintenant en cours d'exécution sur votre machine.

### Méthode 2 : Déploiement avec Docker (Recommandé pour les serveurs)

Idéal pour un hébergement sur un serveur ou un NAS.

1.  **Prérequis** : Assurez-vous d'avoir [Docker](https://www.docker.com/products/docker-desktop/) et Docker Compose installés.
2.  **Créez votre fichier de configuration** :
    - Copiez le fichier `.env.example` et renommez-le en `.env`.
    - Modifiez votre nouveau fichier `.env` pour y mettre vos propres paramètres.
3.  **Lancez avec Docker Compose** :
    ```bash
    docker-compose up -d
    ```
4.  L'addon est maintenant en cours d'exécution dans un conteneur Docker.

## ⚙️ Configuration

Une fois l'addon lancé (avec l'une des deux méthodes), ouvrez votre navigateur et allez à l'adresse suivante :

**`http://<adresse-ip-de-votre-machine>:8000/configure`**

(Si vous l'exécutez sur votre machine locale, ce sera `http://127.0.0.1:8000/configure`).

Sur cette page, vous pourrez :
- Choisir votre service de Debrid.
- Entrer votre clé API.
- Sélectionner d'autres options comme le filtrage des flux.

Une fois la configuration terminée, cliquez sur **"Installer sur Stremio"**.

## 🔧 Variables d'environnement

Toutes les configurations avancées se font via le fichier `.env`.

| Variable                                     | Description                                                                          | Défaut                               |
| -------------------------------------------- | ------------------------------------------------------------------------------------ | ------------------------------------ |
| `ADDON_ID`                                   | (Optionnel) Identifiant unique de l'addon.                                           | `community.fkstream`                 |
| `ADDON_NAME`                                 | (Optionnel) Nom de l'addon affiché dans Stremio.                                     | `FKStream`                           |
| `FASTAPI_HOST`                               | (Optionnel) L'adresse sur laquelle le serveur écoute.                                | `0.0.0.0`                            |
| `FASTAPI_PORT`                               | (Optionnel) Le port sur lequel le serveur écoute.                                    | `8000`                               |
| `FASTAPI_WORKERS`                            | (Optionnel) Nombre de processus. Mettre à -1 pour un calcul automatique.             | `1`                                  |
| `USE_GUNICORN`                               | (Optionnel) Utiliser Gunicorn en production (recommandé sur Linux).                  | `True`                               |
| `DATABASE_TYPE`                              | (Requis) Type de base de données. Options : `sqlite`, `postgresql`.                  | `sqlite`                             |
| `DATABASE_URL`                               | (Requis si `DATABASE_TYPE=postgresql`) URL de connexion PostgreSQL.                  | `user:pass@host:port`                |
| `DATABASE_PATH`                              | (Requis si `DATABASE_TYPE=sqlite`) Chemin vers le fichier de base de données.        | `data/fkstream.db`                   |
| `METADATA_TTL`                               | (Optionnel) Durée de vie du cache pour les métadonnées.                                | `86400` (1 jour)                   |
| `DEBRID_AVAILABILITY_TTL`                    | (Optionnel) Durée de vie du cache pour la disponibilité debrid.                        | `86400` (1 jour)                     |
| `SCRAPE_LOCK_TTL`                            | (Optionnel) Durée de validité d'un verrou de recherche.                                | `300` (5 minutes)                    |
| `SCRAPE_WAIT_TIMEOUT`                        | (Optionnel) Temps d'attente max pour un verrou.                                        | `30` (30 secondes)                   |
| `DEBRID_PROXY_URL`                           | (Optionnel) URL de votre proxy pour contourner les blocages.                           | ` ` (vide)                           |
| `GET_TORRENT_TIMEOUT`                        | (Optionnel) Temps maximum pour télécharger un fichier `.torrent`.                      | `20` (20 secondes)                   |
| `PROXY_DEBRID_STREAM`                        | (Optionnel) Mettre à `True` pour activer le mode proxy.                                | `False`                              |
| `PROXY_DEBRID_STREAM_PASSWORD`               | (Requis si `PROXY_DEBRID_STREAM=True`) Mot de passe pour les utilisateurs.             | `CHANGE_ME`                          |
| `PROXY_DEBRID_STREAM_DEBRID_DEFAULT_SERVICE` | (Requis si `PROXY_DEBRID_STREAM=True`) Votre service debrid.                           | `realdebrid`                         |
| `PROXY_DEBRID_STREAM_DEBRID_DEFAULT_APIKEY`  | (Requis si `PROXY_DEBRID_STREAM=True`) Votre clé API debrid.                           | `CHANGE_ME`                          |
| `CUSTOM_HEADER_HTML`                         | (Optionnel) Code HTML à injecter dans l'en-tête de la page de configuration.         | ` ` (vide)                           |
| `STREMTHRU_URL`                              | (Optionnel) URL du service StremThru.                                                | `https://stremthru.13377001.xyz`     |
| `LOG_LEVEL`                                  | (Optionnel) Niveau de log. Options : `DEBUG`, `PRODUCTION`.                          | `DEBUG`                              |

## 🙏 Remerciements

- Un grand merci à **[g0ldyy]** pour le code original de **[comet](https://github.com/g0ldyy/comet)**.
- Merci à l'équipe de **[Fankai](https://linktr.ee/FanKai)** pour leur travail incroyable sur la bibliothèque de contenu.

---

## 📜 Licence

Le code original de ce projet par g0ldyy est distribué sous la Licence MIT. Les modifications et contributions ultérieures sont également soumises à cette licence. Voir le fichier `LICENSE` pour le texte complet.
