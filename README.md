# Mes-Editos-Radio

Application Streamlit pour suivre, hiérarchiser et ficher les chroniques quotidiennes de France Inter
(L'édito politique, L'édito éco, Géopolitique, et en option L'Invité de 8h20) dans le cadre d'une
préparation au concours de l'INSP.

## Installation

```bash
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # puis renseigner GEMINI_API_KEY
streamlit run app.py
```

Sans clé Gemini, le suivi, la saisie manuelle des fiches et les exports fonctionnent ; seuls les deux
boutons de génération par IA sont désactivés.

## Organisation du code

| Fichier | Rôle |
| --- | --- |
| `app.py` | Interface, onglets et état de session |
| `database.py` | Base SQLite locale (`editos_insp.db`, créée au premier lancement) |
| `rss_fetcher.py` | Lecture des flux RSS, exclusion des dimanches, synchronisation |
| `ai_engine.py` | Fiches depuis l'audio intégral et synthèse mensuelle (SDK `google-genai`) |
| `exporter.py` | Exports Word (`python-docx`) et PDF (`fpdf2`) |
| `styles.css`, `.streamlit/config.toml` | Charte graphique |
| `assets/` | Images de couverture personnalisées, déposées depuis l'onglet Paramètres |

## À savoir

- Les flux Radio France ne remontent que quelques semaines en arrière : l'historique se constitue
  dans la base locale au fil des synchronisations (automatique à l'ouverture, au plus une fois par
  heure, ou forcée depuis l'onglet Paramètres).
- Les nouveaux épisodes arrivent en priorité « 1 - Secondaire ». Les exports « prioritaires »
  retiennent par défaut les priorités 2 et 3 ; ce seuil se règle dans le module d'export.
- Le modèle Gemini utilisé (`gemini-4-hydrogen` par défaut) se change dans l'onglet Paramètres.
- Le PDF utilise une police Unicode du système (Liberation Sans, DejaVu Sans, Noto Sans ou Arial).
  Si aucune n'est installée, il se rabat sur Helvetica en translittérant les caractères hors Latin-1
  (par exemple « œ » devient « oe »).
