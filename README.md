# ModBot — Déploiement Railway

Bot de modération Discord hébergé sur [Railway](https://railway.app).

---

## 📁 Structure du projet

```
├── bot.py              # Code principal du bot
├── requirements.txt    # Dépendances Python
├── Procfile            # Commande de lancement (Railway/Heroku)
├── railway.toml        # Config Railway
├── .gitignore
└── README.md
```

---

## 🚀 Déploiement sur Railway

### 1. Préparer le dépôt GitHub

```bash
git init
git add .
git commit -m "Initial commit — ModBot"
git remote add origin https://github.com/TON_USER/TON_REPO.git
git push -u origin main
```

### 2. Créer le projet sur Railway

1. Va sur [railway.app](https://railway.app) et connecte-toi
2. Clique **New Project → Deploy from GitHub repo**
3. Sélectionne ton dépôt
4. Railway détecte automatiquement Python via Nixpacks

### 3. Ajouter la variable d'environnement

Dans Railway → onglet **Variables** → ajoute :

| Clé | Valeur |
|-----|--------|
| `BOT_TOKEN` | `ton_token_discord_ici` |

> ⚠️ Ne mets jamais ton token directement dans le code ou sur GitHub.

### 4. Vérifier le type de service

Railway peut créer un service **Web** par défaut.  
Pour un bot Discord, il faut un service **Worker** (pas de port HTTP).

- Va dans **Settings → Service → Start Command** et vérifie que c'est bien `python bot.py`
- Ou laisse le `Procfile` gérer ça automatiquement avec `worker:`

### 5. Deploy !

Railway lance le bot automatiquement après chaque push sur `main`.

---

## ⚠️ Limitation importante : persistance des données

Railway utilise un **système de fichiers éphémère** : le dossier `data/` (fichiers JSON) est **effacé à chaque redémarrage**.

### Solutions recommandées :

**Option A — Railway Volume (plus simple)**
1. Dans Railway → **Add Volume**
2. Monte-le sur `/app/data`
3. Les JSON survivent aux redémarrages ✅

**Option B — Variable d'environnement pour le chemin**
Définis `DATA_DIR` comme variable Railway pointant vers un volume persistant.

---

## 🔧 Variables d'environnement disponibles

| Variable | Description | Obligatoire |
|----------|-------------|-------------|
| `BOT_TOKEN` | Token du bot Discord | ✅ Oui |
| `DATA_DIR` | Chemin du dossier de données (défaut : `data`) | Non |

---

## 📋 Commandes du bot (préfixe `+`)

Le bot utilise le préfixe `+`. Exemples :
- `+help` — Aide
- `+ban @user raison` — Bannir
- `+mute @user 10m raison` — Muter
- `+warn @user raison` — Avertir
- `+giveaway` — Lancer un giveaway

---

## 🛠️ Développement local

```bash
# Cloner et installer
pip install -r requirements.txt

# Créer un fichier .env
echo "BOT_TOKEN=ton_token_ici" > .env

# Lancer
python bot.py
```
