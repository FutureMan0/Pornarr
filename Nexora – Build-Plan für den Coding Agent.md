# Nexora – Build-Plan für den Coding Agent

## 1. Projektziel

Baue eine selbst gehostete Mehrbenutzer-Medienplattform, die:

- eine lokale Medienbibliothek durchsucht,
- parallel externe Indexer durchsucht,
- lokale und externe Treffer getrennt darstellt,
- externe Treffer auf Benutzerwunsch an einen vorhandenen Downloader übergibt,
- Downloadstatus und Restzeit live anzeigt,
- Medien automatisch importiert und kategorisiert,
- aus Benutzerinteraktionen lernt,
- passende neue Inhalte kontrolliert automatisch anfordern kann,
- manuelle Requests mit höherer Priorität verarbeitet.

Die Anwendung darf ausschließlich für legal verfügbare Inhalte verwendet werden. Unklare Inhalte müssen vor der Freigabe in eine Admin-Quarantäne gelangen.

---

# 2. Technischer Stack

## Backend

- Python 3.12
- FastAPI
- SQLAlchemy 2
- Alembic
- PostgreSQL
- Redis
- Celery oder Dramatiq
- Pydantic
- HTTPX
- WebSockets oder Server-Sent Events
- pytest

## Frontend

- Next.js
- TypeScript
- React
- Tailwind CSS
- TanStack Query
- Zustand
- Zod
- HLS.js für die Videowiedergabe

## Infrastruktur

- Docker
- Docker Compose
- Nginx oder Caddy
- FFmpeg
- lokales Dateisystem oder NAS
- vorhandener Indexer
- vorhandener Download-Client

---

# 3. Systemkomponenten

## 3.1 Web-Anwendung

Die Web-Anwendung enthält:

- Login
- Startseite
- globale Suche
- lokale Bibliothek
- externe Suchergebnisse
- Detailseiten
- Request-Verwaltung
- Downloadstatus
- Empfehlungen
- Benutzerpräferenzen
- Adminbereich

## 3.2 API-Service

Der API-Service ist zuständig für:

- Authentifizierung
- Benutzerverwaltung
- lokale Suche
- Indexersuche
- Downloadanforderungen
- Bibliotheksverwaltung
- Metadaten
- Kategorisierung
- Empfehlungen
- Benutzerinteraktionen
- Administration

## 3.3 Background Worker

Der Worker übernimmt:

- Indexeranfragen
- Downloadüberwachung
- Dateimport
- Metadatenextraktion
- Thumbnail-Erstellung
- Videotranscodierung
- Duplikaterkennung
- automatische Kategorisierung
- Training des Empfehlungssystems
- automatische Downloadentscheidungen

## 3.4 Integrationsschicht

Implementiere austauschbare Adapter:

```text
IndexerAdapter
DownloaderAdapter
MetadataProviderAdapter
StorageAdapter
RecommendationAdapter
```

Dadurch dürfen Indexer oder Downloader später ersetzt werden, ohne die Kernanwendung umzubauen.

---

# 4. Projektstruktur

```text
nexora/
├── apps/
│   ├── api/
│   ├── web/
│   └── worker/
├── packages/
│   ├── database/
│   ├── integrations/
│   ├── recommendations/
│   ├── shared/
│   └── ui/
├── infrastructure/
│   ├── docker/
│   ├── nginx/
│   └── scripts/
├── media/
│   ├── incoming/
│   ├── library/
│   ├── quarantine/
│   ├── thumbnails/
│   └── transcodes/
├── tests/
├── docker-compose.yml
├── .env.example
└── README.md
```

---

# 5. Datenmodell

## users

```text
id
username
email
password_hash
role
is_active
created_at
updated_at
```

Rollen:

```text
admin
user
```

## media

```text
id
title
normalized_title
description
studio
release_date
duration_seconds
resolution
video_codec
audio_codec
size_bytes
file_path
thumbnail_path
status
metadata_confidence
created_at
updated_at
```

Statuswerte:

```text
processing
available
quarantine
hidden
deleted
```

## performers

```text
id
name
normalized_name
metadata_json
```

## tags

```text
id
name
normalized_name
category
```

## media_performers

```text
media_id
performer_id
```

## media_tags

```text
media_id
tag_id
confidence
source
```

## requests

```text
id
user_id
query
selected_release_id
status
priority
created_at
updated_at
```

Statuswerte:

```text
searching
results_found
queued
downloading
processing
available
failed
not_found
monitoring
cancelled
```

## indexer_results

```text
id
request_id
external_id
indexer_name
title
normalized_title
size_bytes
published_at
quality
download_url
match_score
estimated_download_seconds
estimated_total_seconds
metadata_json
expires_at
```

## download_jobs

```text
id
request_id
downloader_job_id
status
priority
size_bytes
downloaded_bytes
speed_bytes_per_second
eta_seconds
error_message
created_at
updated_at
```

## user_events

```text
id
user_id
media_id
event_type
event_value
created_at
```

Ereignisse:

```text
search
view
play
progress
completed
favorite
unfavorite
request
not_interested
hide_tag
hide_performer
```

## user_preferences

```text
id
user_id
preference_type
entity_id
weight
updated_at
```

## recommendation_candidates

```text
id
user_id
media_id
score
reason_json
model_version
created_at
expires_at
```

## automation_rules

```text
id
user_id
enabled
minimum_score
daily_size_limit_bytes
maximum_concurrent_jobs
allowed_qualities
blocked_tags
blocked_performers
created_at
updated_at
```

---

# 6. Suchfunktion

## 6.1 Suchablauf

Beim Absenden einer Suche:

1. lokale Datenbank sofort durchsuchen,
2. lokale Treffer direkt anzeigen,
3. Indexersuche im Hintergrund starten,
4. Indexertreffer nachladen,
5. Duplikate und bereits vorhandene Medien markieren,
6. Downloadzeit berechnen,
7. Ergebnisse nach Relevanz sortieren.

## 6.2 API-Endpunkte

```http
GET /api/search/local?q={query}
POST /api/search/indexers
GET /api/search/jobs/{job_id}
GET /api/search/jobs/{job_id}/events
```

Request:

```json
{
  "query": "Suchbegriff",
  "quality": ["1080p", "2160p"],
  "maximum_size_bytes": 20000000000
}
```

Response:

```json
{
  "job_id": "uuid",
  "status": "searching"
}
```

## 6.3 Darstellung

Die Suchseite erhält zwei Bereiche:

```text
Lokal verfügbar
Extern gefunden
```

Lokale Treffer zeigen:

- Titel
- Thumbnail
- Qualität
- Dateigröße
- Laufzeit
- Sofort-abspielen-Schaltfläche

Externe Treffer zeigen:

- Titel
- Indexer
- Qualität
- Größe
- Alter des Ergebnisses
- Match-Score
- geschätzte Downloadzeit
- geschätzte Gesamtzeit inklusive Queue
- Download-Schaltfläche

## 6.4 Downloadzeit

Berechnung:

```text
Downloadzeit =
Dateigröße /
gewichtete effektive Downloadgeschwindigkeit
```

Die effektive Geschwindigkeit wird aus folgenden Daten bestimmt:

```text
50 % Durchschnitt der letzten 5 Minuten
30 % Durchschnitt der letzten 10 Downloads
20 % konfigurierter Maximalwert
```

Gesamtzeit:

```text
priorisierte Queue-Zeit
+ eigene Downloadzeit
+ erwartete Entpackzeit
+ erwartete Importzeit
```

Die Schätzung muss regelmäßig aktualisiert werden.

---

# 7. Download-Workflow

## 7.1 Manueller Request

Beim Klick auf „Herunterladen“:

1. Benutzerberechtigung prüfen.
2. Release erneut validieren.
3. Duplikatprüfung ausführen.
4. Request-Datensatz erstellen.
5. Job an Downloader übergeben.
6. Request priorisieren.
7. Live-Status aktualisieren.
8. Nach erfolgreichem Download Import-Job starten.
9. Medium nach Verarbeitung verfügbar machen.

## 7.2 Prioritäten

```text
100 Admin-Request
90 Benutzer-Request
60 abonnierte Inhalte
40 Empfehlungen
20 Hintergrunddownloads
```

## 7.3 Fehlerbehandlung

Bei einem fehlgeschlagenen Download:

1. Fehler speichern.
2. Release temporär blockieren.
3. alternativen Treffer suchen,
4. maximal zwei automatische Wiederholungen,
5. danach Benutzer und Admin informieren.

---

# 8. Import-Pipeline

## Schritt 1: Eingang prüfen

- Dateiendung prüfen
- Dateigröße prüfen
- Archivstatus prüfen
- Malware-Scan optional ausführen
- unbekannte oder unvollständige Dateien blockieren

## Schritt 2: Medieninformationen extrahieren

FFprobe verwenden für:

- Auflösung
- Codecs
- Laufzeit
- Bitrate
- Streams
- Containerformat

## Schritt 3: Metadaten erkennen

Quellen:

- Release-Titel
- Ordnername
- Dateiname
- NFO-Datei
- Indexer-Metadaten
- vorhandene Metadatenanbieter

## Schritt 4: Titel normalisieren

Entfernen beziehungsweise vereinheitlichen:

- Release-Gruppen
- Auflösung
- Codecbezeichnungen
- Sonderzeichen
- mehrfach vorkommende Leerzeichen
- Datumsvarianten
- bekannte technische Tokens

## Schritt 5: Duplikate prüfen

Vergleiche:

- normalisierter Titel
- Studio
- Veröffentlichungsdatum
- Darsteller
- Laufzeit
- Dateigröße
- Prüfsumme
- optional Video-Fingerprint

## Schritt 6: Kategorisieren

Kategorien aus:

- Titel-Keywords
- Indexer-Tags
- Metadaten
- Benutzerkorrekturen
- vorhandenen Regeln

Jede Zuordnung enthält:

```text
Tag
Konfidenzwert
Quelle
Modellversion
```

## Schritt 7: Quarantäneentscheidung

In Quarantäne verschieben, wenn:

- Metadatenkonfidenz zu niedrig ist,
- Inhalt nicht eindeutig identifiziert wurde,
- blockierte Begriffe erkannt wurden,
- Dateityp unerwartet ist,
- Alters- oder Einwilligungsnachweise unklar sind,
- Duplikatprüfung widersprüchlich ist.

## Schritt 8: Vorschaubilder

FFmpeg erzeugt:

- Poster
- mehrere Preview-Bilder
- optional kurze Vorschausequenz

## Schritt 9: Import

Dateien nach folgendem Schema speichern:

```text
/library/{studio}/{year}/{normalized_title}/{quality}/video.ext
```

---

# 9. Empfehlungssystem

## 9.1 MVP-Modell

Zunächst ein regelbasiertes, inhaltsbasiertes Modell implementieren.

Bewertete Merkmale:

- Tags
- Darsteller
- Studios
- Qualität
- Veröffentlichungsjahr
- Laufzeit
- wiederholte Wiedergaben
- Favoriten
- Requests
- „Nicht interessiert“

Beispielgewichte:

```text
Request                 +10
Favorit                  +8
vollständig angesehen    +5
mehrfach angesehen       +4
mehr als 50 % angesehen  +3
angeklickt               +1
früh abgebrochen         -2
nicht interessiert       -8
explizit blockiert     -100
```

## 9.2 Nutzerprofil

Erzeuge pro Benutzer ein gewichtetes Interessenprofil:

```json
{
  "tags": {
    "tag_a": 0.83,
    "tag_b": 0.51
  },
  "performers": {
    "performer_a": 0.77
  },
  "studios": {
    "studio_a": 0.64
  },
  "quality": {
    "2160p": 0.72
  }
}
```

## 9.3 Empfehlungsberechnung

```text
Recommendation Score =
0,30 × Tag-Übereinstimmung
+ 0,25 × Darsteller-Übereinstimmung
+ 0,15 × Studio-Übereinstimmung
+ 0,10 × Qualitätspräferenz
+ 0,10 × Aktualität
+ 0,10 × allgemeine Nutzung
- Blockierungsfaktoren
```

Jede Empfehlung muss eine erklärbare Begründung enthalten:

```text
Empfohlen, weil der Benutzer ähnliche Tags und dasselbe Studio häufig auswählt.
```

## 9.4 Spätere Erweiterung

Nach ausreichenden Interaktionsdaten:

- implizites kollaboratives Filtern
- Matrix-Faktorisierung
- Benutzer- und Item-Embeddings
- hybrides Ranking
- zeitabhängige Interessen
- Diversitätsfaktor
- Exploration versus Exploitation

Das Modell darf keine sensiblen Benutzerpräferenzen zwischen Accounts offenlegen.

---

# 10. Automatische Downloads

Automatische Downloads müssen standardmäßig deaktiviert sein.

## Voraussetzungen

Ein Kandidat darf nur automatisch heruntergeladen werden, wenn:

- Automatisierung für den Benutzer aktiviert ist,
- Score über dem Mindestwert liegt,
- kein blockierter Tag vorhanden ist,
- kein Duplikat vorhanden ist,
- tägliches Speicherbudget nicht überschritten wird,
- ausreichend freier Speicher vorhanden ist,
- Downloadqualität den Regeln entspricht,
- Metadatenkonfidenz ausreichend hoch ist.

## Beispielscore

```text
Auto-Download Score =
0,40 × Empfehlungsscore
+ 0,20 × Metadatenqualität
+ 0,15 × Releasequalität
+ 0,10 × Indexer-Zuverlässigkeit
+ 0,10 × Aktualität
- 0,15 × Speicherbedarf
- 0,20 × Duplikatrisiko
- 0,10 × erwartete Downloadzeit
```

## Sicherheitsgrenzen

Standardwerte:

```text
maximal 10 GB pro Benutzer und Tag
maximal 2 automatische Jobs gleichzeitig
mindestens 15 % freier Speicher
maximal 3 automatische Downloads pro Tag
manuelle Requests immer bevorzugen
```

---

# 11. Live-Updates

Verwende WebSockets oder Server-Sent Events.

Events:

```text
search.started
search.result_added
search.completed
request.created
download.queued
download.started
download.progress
download.completed
download.failed
import.started
import.completed
media.available
```

Beispiel:

```json
{
  "event": "download.progress",
  "job_id": "uuid",
  "progress": 47.3,
  "speed_bytes_per_second": 12500000,
  "eta_seconds": 840
}
```

---

# 12. Benutzeroberfläche

## Seiten

```text
/login
/
/search
/library
/media/{id}
/requests
/downloads
/recommendations
/settings
/admin
/admin/quarantine
/admin/integrations
```

## Startseite

Bereiche:

- Weiterschauen
- Neu hinzugefügt
- Für dich empfohlen
- aktive Downloads
- letzte Requests

## Suchseite

Ablauf:

1. Suchfeld oben.
2. lokale Treffer sofort.
3. Ladeindikator für Indexer.
4. externe Treffer darunter.
5. Filter für Qualität, Größe, Alter und Indexer.
6. Sortierung nach Relevanz, Zeit, Größe und Qualität.

## Request-Seite

Zeige:

- Suchbegriff
- ausgewähltes Release
- Status
- Fortschritt
- aktuelle Geschwindigkeit
- Restzeit
- Importstatus
- Fehlermeldungen
- Abbrechen-Schaltfläche

## Adminbereich

Funktionen:

- Benutzer verwalten
- Downloader konfigurieren
- Indexer konfigurieren
- Queue verwalten
- Quarantäne prüfen
- blockierte Begriffe verwalten
- Speicherstatus anzeigen
- Jobs erneut starten
- Logs ansehen
- Empfehlungssystem zurücksetzen

---

# 13. Authentifizierung und Sicherheit

Implementieren:

- sichere Passwort-Hashes mit Argon2
- HTTP-only Session-Cookies
- CSRF-Schutz
- Rate Limits
- Rollen- und Rechteprüfung
- Audit-Log
- verschlüsselte API-Zugangsdaten
- keine Secrets im Frontend
- keine Secrets in Logs
- Pfadvalidierung gegen Directory Traversal
- Dateitypvalidierung
- Größenlimits
- sichere Proxy-Konfiguration

Jede administrative Aktion wird protokolliert.

---

# 14. Konfiguration

`.env.example`:

```env
APP_ENV=development
APP_SECRET=
DATABASE_URL=postgresql+psycopg://nexora:nexora@postgres:5432/nexora
REDIS_URL=redis://redis:6379/0

MEDIA_INCOMING_PATH=/media/incoming
MEDIA_LIBRARY_PATH=/media/library
MEDIA_QUARANTINE_PATH=/media/quarantine
MEDIA_THUMBNAIL_PATH=/media/thumbnails

INDEXER_BASE_URL=
INDEXER_API_KEY=

DOWNLOADER_BASE_URL=
DOWNLOADER_API_KEY=

MIN_FREE_DISK_PERCENT=15
DEFAULT_DAILY_DOWNLOAD_LIMIT_GB=10
DEFAULT_AUTO_DOWNLOADS_ENABLED=false
```

---

# 15. Entwicklungsphasen

## Phase 1: Grundgerüst

Ziele:

- Monorepo erstellen
- Docker Compose erstellen
- FastAPI starten
- Next.js starten
- PostgreSQL und Redis anbinden
- Migrationen einrichten
- Authentifizierung implementieren
- Healthchecks erstellen

Abnahmekriterien:

- Benutzer kann sich anmelden.
- API, Worker und Web-App laufen über Docker Compose.
- Datenbankmigrationen funktionieren.
- Healthcheck zeigt Status aller Dienste.

## Phase 2: Lokale Bibliothek

Ziele:

- Medienmodell
- Import vorhandener Dateien
- Metadatenextraktion
- Thumbnails
- lokale Suche
- Detailseite
- Videowiedergabe

Abnahmekriterien:

- lokale Medien werden indexiert,
- Suche findet Titel und Tags,
- Detailseite zeigt Metadaten,
- Video kann abgespielt werden.

## Phase 3: Indexerintegration

Ziele:

- IndexerAdapter
- asynchrone Indexersuche
- Normalisierung der Ergebnisse
- Match-Score
- Live-Nachladen
- Ergebnis-Cache

Abnahmekriterien:

- lokale Treffer erscheinen zuerst,
- externe Treffer erscheinen darunter,
- Indexerfehler blockieren lokale Suche nicht,
- Ergebnisse werden dedupliziert.

## Phase 4: Downloaderintegration

Ziele:

- DownloaderAdapter
- Download erstellen
- Queue lesen
- Fortschritt lesen
- Restzeit berechnen
- Jobs abbrechen
- Live-Status

Abnahmekriterien:

- ein externes Ergebnis kann angefordert werden,
- der Download erscheint in der Queue,
- Fortschritt und Restzeit aktualisieren sich,
- abgeschlossene Downloads starten den Import.

## Phase 5: Import und Kategorisierung

Ziele:

- Dateibeobachtung
- Import-Pipeline
- FFprobe
- Normalisierung
- Duplikaterkennung
- Tags
- Quarantäne
- Adminfreigabe

Abnahmekriterien:

- fertige Downloads werden automatisch erkannt,
- Metadaten werden extrahiert,
- sichere Treffer landen in der Bibliothek,
- unklare Treffer landen in Quarantäne.

## Phase 6: Requests

Ziele:

- Request-Modell
- Request-Status
- Suchwiederholung
- Prioritäten
- „nicht gefunden“-Überwachung
- Benutzerbenachrichtigung innerhalb der App

Abnahmekriterien:

- Benutzer kann einen Request erstellen,
- Requests erhalten höhere Priorität,
- nicht gefundene Requests werden erneut gesucht,
- Status ist jederzeit nachvollziehbar.

## Phase 7: Empfehlungen

Ziele:

- Benutzerereignisse
- Interessenprofil
- regelbasiertes Ranking
- Empfehlungsseite
- „Nicht interessiert“
- Erklärungen

Abnahmekriterien:

- Empfehlungen unterscheiden sich je Benutzer,
- Blockierungen werden respektiert,
- jede Empfehlung enthält einen Grund,
- Benutzerfeedback beeinflusst neue Empfehlungen.

## Phase 8: Kontrollierte Automatisierung

Ziele:

- Automatisierungsregeln
- Speicherbudgets
- Score-Schwellenwert
- Tageslimits
- Queue-Limits
- Audit-Log

Abnahmekriterien:

- automatische Downloads sind standardmäßig aus,
- aktivierte Automatisierung respektiert Limits,
- manuelle Requests bleiben priorisiert,
- jede automatische Entscheidung ist erklärbar.

## Phase 9: Stabilisierung

Ziele:

- End-to-End-Tests
- Lasttests
- Fehlerseiten
- Backups
- Wiederherstellungsanleitung
- Monitoring
- strukturierte Logs
- Sicherheitsprüfung

---

# 16. Teststrategie

## Unit-Tests

Testen:

- Titelnormalisierung
- Downloadzeitberechnung
- Match-Score
- Empfehlungsscore
- Duplikaterkennung
- Prioritätslogik
- Speicherlimits

## Integrationstests

Testen:

- IndexerAdapter
- DownloaderAdapter
- Datenbank
- Redis
- Import-Pipeline
- WebSocket-Events

Externe Systeme in Tests mocken.

## End-to-End-Tests

Mit Playwright:

- Login
- Suche
- lokale Treffer
- externe Treffer
- Download starten
- Status verfolgen
- Medium öffnen
- Empfehlung bewerten
- Admin-Quarantäne freigeben

---

# 17. Coding-Regeln für den Agenten

1. Type Hints im gesamten Python-Code verwenden.
2. TypeScript im Strict Mode verwenden.
3. Keine Secrets hardcoden.
4. Externe Integrationen nur über Adapter ansprechen.
5. Jede API-Eingabe mit Pydantic oder Zod validieren.
6. Datenbankänderungen nur über Migrationen durchführen.
7. Hintergrundjobs müssen idempotent sein.
8. Jeder Job benötigt Retry- und Timeout-Regeln.
9. Fehler strukturiert protokollieren.
10. Keine personenbezogenen Interessen in Klartext-Logs schreiben.
11. Keine automatische Veröffentlichung bei niedriger Metadatenkonfidenz.
12. Für jede neue Funktion Tests ergänzen.
13. README und API-Dokumentation aktuell halten.
14. Kleine, nachvollziehbare Commits erstellen.
15. Nach jeder Phase einen lauffähigen Stand sicherstellen.

---

# 18. Erste Aufgaben für den Coding Agenten

## Task 1

Initialisiere das Monorepo mit:

- FastAPI
- Next.js
- PostgreSQL
- Redis
- Worker
- Docker Compose
- Makefile
- `.env.example`

## Task 2

Implementiere:

```http
GET /health
GET /api/health
```

Die Antwort soll den Status von API, Datenbank, Redis und Worker enthalten.

## Task 3

Implementiere Benutzer, Rollen und Login.

## Task 4

Implementiere das Medienmodell und einen lokalen Bibliotheksscanner.

## Task 5

Implementiere:

```http
GET /api/search/local?q=
```

## Task 6

Definiere die Interfaces:

```python
class IndexerAdapter:
    async def search(self, query: SearchQuery) -> list[IndexerResult]:
        ...

class DownloaderAdapter:
    async def add_download(self, release: IndexerResult) -> DownloadJob:
        ...

    async def get_status(self, job_id: str) -> DownloadStatus:
        ...

    async def cancel(self, job_id: str) -> None:
        ...
```

## Task 7

Erstelle Mock-Adapter für lokale Entwicklung und automatisierte Tests.

## Task 8

Implementiere die Suchseite mit lokalen Ergebnissen und simulierten Indexertreffern.

Erst danach reale Indexer- und Downloaderverbindungen ergänzen.

---

# 19. Definition of Done

Eine Funktion ist abgeschlossen, wenn:

- Backend und Frontend implementiert sind,
- Eingaben validiert werden,
- Berechtigungen geprüft werden,
- Fehlerzustände behandelt werden,
- Unit- oder Integrationstests existieren,
- Logging vorhanden ist,
- Dokumentation aktualisiert wurde,
- Docker-Build erfolgreich ist,
- Linter und Typechecker erfolgreich sind,
- keine Secrets oder sensiblen Daten eingecheckt wurden.

---

# 20. MVP-Abschlusskriterien

Das MVP ist fertig, wenn ein Benutzer:

1. sich anmelden kann,
2. lokale Medien durchsuchen kann,
3. lokale Ergebnisse sofort sieht,
4. externe Indexertreffer darunter sieht,
5. für externe Treffer eine Download- und Gesamtzeit sieht,
6. einen Treffer anfordern kann,
7. den Downloadfortschritt live verfolgen kann,
8. das importierte Medium anschließend lokal findet,
9. Empfehlungen auf Basis eigener Interaktionen erhält,
10. unerwünschte Kategorien oder Treffer blockieren kann.

Automatische Downloads gehören erst nach erfolgreichem MVP-Test und aktivierter Benutzerfreigabe zum produktiven Betrieb.