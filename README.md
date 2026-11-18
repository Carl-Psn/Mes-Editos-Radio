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

Sans clé Gemini, le suivi, l'import ou la saisie manuelle des fiches et les exports fonctionnent ; seuls les
deux boutons de génération par IA sont désactivés.

Dans l'atelier de fichage, chaque épisode accepte aussi l'import d'une fiche existante au format **Markdown**
(`.md`) ou **PDF** (extraction du texte pour édition et export).

## Organisation du code

| Fichier | Rôle |
| --- | --- |
| `app.py` | Interface, onglets et état de session |
| `database.py` | Base SQLite locale (`editos_insp.db`, créée au premier lancement) |
| `rss_fetcher.py` | Lecture des flux RSS, exclusion des dimanches, synchronisation |
| `ai_engine.py` | Fiches depuis la retranscription Radio France (défaut) ou l'audio, synthèse mensuelle (`google-genai`) |
| `transcript_fetcher.py` | Extraction de la retranscription sur la page web de l'épisode |
| `exporter.py` | Exports Word (`python-docx`) et PDF (`fpdf2`) |
| `fiche_import.py` | Import de fiches manuelles (Markdown, PDF via `pypdf`) |
| `styles.css`, `.streamlit/config.toml` | Charte graphique |
| `assets/` | Images de couverture personnalisées, déposées depuis l'onglet Paramètres |

## À savoir

- Les flux Radio France ne remontent que quelques semaines en arrière : l'historique se constitue
  dans la base locale au fil des synchronisations (automatique à l'ouverture, au plus une fois par
  heure, ou forcée depuis l'onglet Paramètres).
- Les nouveaux épisodes arrivent en priorité « 1 - Secondaire ». Les exports « prioritaires »
  retiennent par défaut les priorités 2 et 3 ; ce seuil se règle dans le module d'export.
- Les fiches IA s'appuient par défaut sur la **retranscription** de la page `radiofrance.fr` de l'épisode (plus rapide que l'audio). Le mode audio reste disponible dans Paramètres.
- Le modèle Gemini utilisé (`gemini-3.5-flash-lite` par défaut) se change dans l'onglet Paramètres ; en cas d'erreur 503, l'app réessaie et peut basculer sur un modèle de secours.
- L'onglet **Modifier le texte** de l'atelier propose un éditeur visuel (gras, italique, souligné, couleur, titres,
  citation, listes à trois niveaux) : inutile de connaître le Markdown pour rédiger ou corriger une fiche à la main.
- L'interface s'adapte aux téléphones (jusqu'à 640 px de large : onglets épinglés, cartes compactes, liste
  de l'atelier au-dessus de l'espace de fichage) et aux tablettes (deux cartes par ligne). Les règles sont
  regroupées à la fin de `styles.css`.
- Le PDF utilise une police Unicode du système (Liberation Sans, DejaVu Sans, Noto Sans ou Arial).
  Si aucune n'est installée, il se rabat sur Helvetica en translittérant les caractères hors Latin-1
  (par exemple « œ » devient « oe »).
