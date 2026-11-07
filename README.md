# SamaFile - backend-ci

Backend de SamaFile (gestion numérique des files d'attente) : **une seule API FastAPI**, dans **un seul conteneur Docker**,
qui utilise une base PostgreSQL placée dans un conteneur séparé (avec volume persistant).

| Élément | Valeur |
|---|---|
| Technologie | Python 3.12, FastAPI, SQLAlchemy |
| Image Docker | `backend` (Docker Hub : `<utilisateur>/backend:<tag>`) |
| Port dans le conteneur | 8000 |
| Documentation Swagger | `/docs` (ex. http://localhost:8000/docs) |
| Health check | `/health` |

## Structure

```
backend-ci/
├── Dockerfile
├── Jenkinsfile          pipeline CI (à ajouter avec M3)
├── requirements.txt
├── src/                 code applicatif
│   ├── main.py          routes, CORS, format d'erreur
│   ├── logic.py         règles métier (FIFO, horaires, absence, expiration)
│   ├── models.py        tables PostgreSQL
│   ├── schemas.py       formats JSON (contrat d'API)
│   └── database.py      connexion à PostgreSQL
├── tests/               tests unitaires et API (Pytest)
└── README.md
```

## Base de données : PostgreSQL

L'adresse de la base est écrite dans `src/database.py` :

```python
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://projetdevops:projet@postgres_server:5432/samafile",
)
```

- Identifiant `projetdevops`, mot de passe `projet`, base `samafile`.
- Hôte `postgres_server` = le conteneur PostgreSQL. Selon l'endroit où tourne le backend, changer l'hôte :
  `localhost` (uvicorn sur la machine) ou `db` (Docker Compose du dépôt `deployment-cd`).
  Ces trois adresses sont déjà dans `database.py` (deux sont en commentaire).
- Au premier démarrage, le backend crée les tables et 4 services de démonstration (voir plus bas).

Créer le compte et les bases une seule fois (pgAdmin, Query Tool, **une commande à la fois**) :

```sql
CREATE ROLE projetdevops LOGIN PASSWORD 'projet';
CREATE DATABASE samafile OWNER projetdevops;
CREATE DATABASE samafile_test OWNER projetdevops;
```

## Lancer le backend dans Docker

Le conteneur du backend doit être sur le même réseau Docker que `postgres_server` :

```
docker network create samafile
docker network connect samafile postgres_server

docker build -t samafile/backend .
docker run -d --name backend --network samafile -p 8000:8000 samafile/backend
```

Tester : http://localhost:8000/health doit répondre `{"status":"ok"}`, puis http://localhost:8000/docs.
Les données restent dans le volume de PostgreSQL : ne jamais le supprimer (`docker compose down -v` interdit).

## Tests

Les tests vident les tables : ils utilisent la base dédiée `samafile_test` (le nom doit contenir `test`),
par défaut `postgresql://projetdevops:projet@localhost:5432/samafile_test`
(le port 5432 de `postgres_server` doit être publié). Autre base : variable `TEST_DATABASE_URL`.

```
pip install -r tests/requirements.txt
python -m pytest tests
```

---

# API pour le frontend (contrat avec M2)

## Règles générales

| Sujet | Valeur |
|---|---|
| URL | Une seule adresse pour toute l'API : `http://localhost:8000` (tickets, services et guichets). Tous les chemins commencent par `/api`. |
| Format | JSON, noms de champs en `snake_case`, tous les chemins commencent par `/api` |
| Dates | ISO 8601 en UTC avec `Z`, ex. `2026-11-09T10:00:00Z`. Le Sénégal est à UTC+0 : l'heure locale est identique. |
| CORS | Ouvert à toutes les origines |
| Identifiants | Entiers (`id: 25`) |
| Authentification | Aucune dans le MVP |
| Actualisation | Le frontend rafraîchit par polling (toutes les 5 secondes) : pas de WebSocket |
| Messages | En français, prêts à afficher (champ `message`) |

## Statuts d'un ticket

| Statut | Signification | Passe à |
|---|---|---|
| `WAITING` | En attente dans la file | `CALLED`, `CANCELLED`, `EXPIRED` |
| `CALLED` | Appelé, doit se présenter au guichet | `IN_PROGRESS`, `NO_SHOW`, `CANCELLED`, `EXPIRED` |
| `IN_PROGRESS` | En cours de traitement | `COMPLETED` |
| `COMPLETED` | Terminé (final) | - |
| `NO_SHOW` | Absent | `WAITING` (réactivation) |
| `CANCELLED` | Annulé (final) | - |
| `EXPIRED` | Expiré à la fermeture du service (final) | - |

Toute autre transition renvoie une erreur `409 INVALID_TRANSITION`.

## Parcours à implémenter

**Usager :** `GET /api/services` (choisir un service ouvert) puis `POST /api/tickets`
puis `GET /api/tickets/{id}` toutes les 5 s (statut, position, message, et bannière `notification` si elle n'est pas `null`).

**Agent :** `GET /api/counters` (choisir son guichet) puis `GET /api/counters/{id}/tickets` (la file)
puis `POST /api/counters/{id}/next` (appeler) puis `PATCH /api/tickets/{id}/start` puis `PATCH /api/tickets/{id}/complete`.
Si l'usager ne vient pas : `PATCH /api/tickets/{id}/no-show` (autorisé seulement quand le délai est écoulé).
Le délai restant se calcule avec `called_at` + `no_show_timeout_seconds` du service.

## Données de démonstration (créées au premier démarrage)

| Service (`id`) | Lettre des tickets | Guichets (`id`) | Horaires |
|---|---|---|---|
| Dépôt (1) | A | 1 et 2 | 08:00 - 16:00 |
| Retrait (2) | B | 3 et 4 | 08:00 - 16:00 |
| Ouverture de compte (3) | C | 5 et 6 | 08:00 - 16:00 |
| Renseignements (4) | D | 7 et 8 | 08:00 - 16:00 |

Le numéro d'un ticket est `lettre du service` + numéro du jour sur 3 chiffres (`A001`, `A002`...), et repart à `001` chaque jour.

## Routes : services et tickets

| Méthode | Route | But | Succès |
|---|---|---|---|
| GET | `/health` | Santé du backend et de la base | 200 |
| GET | `/api/services` | Liste des services (avec ouverture) | 200 |
| GET | `/api/services/{id}` | Un service | 200 |
| POST | `/api/tickets` | Créer un ticket | 201 |
| GET | `/api/tickets` | Lister les tickets (filtres) | 200 |
| GET | `/api/tickets/{id}` | Détail d'un ticket | 200 |
| GET | `/api/tickets/{id}/position` | Position dans la file | 200 |
| PATCH | `/api/tickets/{id}/start` | `CALLED` vers `IN_PROGRESS` | 200 |
| PATCH | `/api/tickets/{id}/complete` | `IN_PROGRESS` vers `COMPLETED` | 200 |
| PATCH | `/api/tickets/{id}/no-show` | `CALLED` vers `NO_SHOW`, appelle le suivant | 200 |
| PATCH | `/api/tickets/{id}/cancel` | `WAITING` ou `CALLED` vers `CANCELLED` | 200 |
| POST | `/api/tickets/{id}/reactivate` | `NO_SHOW` vers `WAITING`, en fin de file | 200 |

### GET /api/services/{id}

```json
{
  "id": 1,
  "name": "Dépôt",
  "opening_time": "08:00",
  "closing_time": "16:00",
  "is_open": true,
  "ticket_distribution_open": true,
  "no_show_timeout_seconds": 120
}
```

`GET /api/services` renvoie la liste de ces objets. Désactiver le bouton « Prendre un ticket » quand `ticket_distribution_open` vaut `false`.

### POST /api/tickets

Corps : `service_id` obligatoire, `user_name` facultatif (100 caractères maximum, ticket anonyme si absent).

```json
{ "service_id": 1, "user_name": "Awa Ndiaye" }
```

Réponse `201` :

```json
{
  "id": 1,
  "ticket_number": "A001",
  "status": "WAITING",
  "position": 1,
  "message": "Votre ticket a été créé."
}
```

Erreurs : `404 SERVICE_NOT_FOUND`, `409 SERVICE_CLOSED` (hors horaires, le `message` indique l'heure d'ouverture), `422 VALIDATION_ERROR`.

### GET /api/tickets/{id}

```json
{
  "id": 1,
  "ticket_number": "A001",
  "service_id": 1,
  "counter_id": null,
  "status": "WAITING",
  "created_at": "2026-11-09T10:00:00Z",
  "called_at": null,
  "user_name": "Awa Ndiaye",
  "position": 1,
  "people_ahead": 0,
  "notification": "Vous êtes le prochain : présentez-vous près du guichet.",
  "message": "Votre ticket est en attente."
}
```

`position`, `people_ahead` et `notification` ne sont renseignés que si le statut est `WAITING` (sinon `null`).
`counter_id` et `called_at` sont renseignés à partir de l'appel. Voir la section « Notification » ci-dessous.
Erreur : `404 TICKET_NOT_FOUND`.

### GET /api/tickets/{id}/position

```json
{
  "id": 2,
  "ticket_number": "A002",
  "status": "WAITING",
  "position": 2,
  "people_ahead": 1,
  "notification": "Plus qu'une personne devant vous. Rapprochez-vous du service."
}
```

### GET /api/tickets

Filtres facultatifs : `service_id`, `counter_id`, `status` (un des 7 statuts), `limit` (1 à 200, défaut 50), `offset` (défaut 0).
Tri : ordre de la file (FIFO).

```json
{
  "items": [
    {
      "id": 1,
      "ticket_number": "A001",
      "service_id": 1,
      "counter_id": null,
      "status": "WAITING",
      "created_at": "2026-11-09T10:00:00Z",
      "called_at": null
    }
  ],
  "total": 1
}
```

Un `status` inconnu renvoie `422 VALIDATION_ERROR`.

### Notification « rapprochez-vous »

Quand il reste **10 personnes ou moins** devant un ticket `WAITING`, le backend ajoute un message dans le champ
`notification` de `GET /api/tickets/{id}` et `GET /api/tickets/{id}/position`. Sinon, `notification` vaut `null`.

| `people_ahead` | `notification` |
|---|---|
| plus de 10 | `null` |
| 10 | `"Plus que 10 personnes devant vous. Rapprochez-vous du service."` |
| 1 | `"Plus qu'une personne devant vous. Rapprochez-vous du service."` |
| 0 | `"Vous êtes le prochain : présentez-vous près du guichet."` |

- `people_ahead` = `position` - 1 : nombre de tickets `WAITING` du même service passés avant le sien.
- **Côté frontend :** au polling (toutes les 5 s), afficher une bannière bien visible dès que `notification` n'est pas `null`,
  avec le texte tel quel. Le message se met à jour tout seul quand la file avance.
- La notification s'affiche seulement quand la page de suivi est ouverte : pas de SMS ni de notification hors application dans le MVP.
- Le seuil (10) est la constante `APPROACH_THRESHOLD` dans `src/logic.py`.

### start, complete, cancel, reactivate

Aucun corps. Réponse `200` : le ticket mis à jour, au même format que `GET /api/tickets/{id}`.
Erreurs : `404 TICKET_NOT_FOUND`, `409 INVALID_TRANSITION`, et pour `reactivate` aussi `409 SERVICE_CLOSED`.
Après `reactivate`, le ticket est `WAITING` et placé **en fin de file**.

### PATCH /api/tickets/{id}/no-show

Aucun corps. Autorisé seulement si le délai du service (120 s par défaut) est écoulé depuis `called_at`.
Le backend appelle ensuite automatiquement le ticket suivant : il est renvoyé dans `next_ticket`
(`null` si la file est vide). Réponse `200` :

```json
{
  "id": 1,
  "ticket_number": "A001",
  "service_id": 1,
  "counter_id": 1,
  "status": "NO_SHOW",
  "created_at": "2026-11-09T10:00:00Z",
  "called_at": "2026-11-09T10:00:00Z",
  "user_name": "Awa Ndiaye",
  "position": null,
  "people_ahead": null,
  "notification": null,
  "message": "Vous avez été déclaré absent.",
  "next_ticket": {
    "id": 2,
    "ticket_number": "A002",
    "service_id": 1,
    "counter_id": 1,
    "status": "CALLED",
    "created_at": "2026-11-09T10:00:00Z",
    "called_at": "2026-11-09T10:02:01Z",
    "user_name": null,
    "position": null,
    "people_ahead": null,
    "notification": null,
    "message": "Votre ticket est appelé. Présentez-vous au guichet."
  }
}
```

Erreurs : `409 NO_SHOW_TOO_EARLY` (avec `details.remaining_seconds`), `409 INVALID_TRANSITION`, `404 TICKET_NOT_FOUND`.

## Routes : guichets

| Méthode | Route | But | Succès |
|---|---|---|---|
| GET | `/api/counters` | Liste des guichets (filtre `service_id`) | 200 |
| GET | `/api/counters/{id}/tickets` | File du guichet | 200 |
| POST | `/api/counters/{id}/next` | Appeler le prochain ticket | 200 |

### GET /api/counters

```json
[
  { "id": 1, "name": "Dépôt - guichet 1", "service_id": 1, "service_name": "Dépôt", "is_active": true },
  { "id": 2, "name": "Dépôt - guichet 2", "service_id": 1, "service_name": "Dépôt", "is_active": true }
]
```

### GET /api/counters/{id}/tickets

Tickets du service du guichet, dans l'ordre FIFO. Paramètres facultatifs : `status` (défaut `WAITING`), `limit`, `offset`.
Réponse au même format que `GET /api/tickets` (`items` et `total`). Erreur : `404 COUNTER_NOT_FOUND`.

### POST /api/counters/{id}/next

Aucun corps. Le plus ancien ticket `WAITING` du service passe à `CALLED` et est rattaché au guichet.
Réponse `200` : le ticket appelé, au même format que `GET /api/tickets/{id}` (`status: "CALLED"`, `counter_id`, `called_at` renseignés).

Erreurs : `404 COUNTER_NOT_FOUND`, `409 QUEUE_EMPTY` (aucun ticket en attente),
`409 COUNTER_BUSY` (le guichet traite déjà un ticket : le terminer d'abord), `409 COUNTER_INACTIVE`.

## Format des erreurs

Toutes les erreurs ont la même forme :

```json
{ "error": { "code": "SERVICE_CLOSED", "message": "Le service est fermé. Aucun ticket ne peut être attribué.", "details": null } }
```

Le frontend teste `error.code` et affiche `error.message`. `details` vaut `null` sauf pour
`VALIDATION_ERROR` (liste `{field, message}`) et `NO_SHOW_TOO_EARLY` (`remaining_seconds`).

| Code | HTTP | Cas |
|---|---|---|
| `VALIDATION_ERROR` | 422 | Corps ou paramètre invalide |
| `SERVICE_NOT_FOUND` | 404 | Service inexistant ou inactif |
| `TICKET_NOT_FOUND` | 404 | Ticket inexistant |
| `COUNTER_NOT_FOUND` | 404 | Guichet inexistant |
| `NOT_FOUND` | 404 | Route inexistante |
| `SERVICE_CLOSED` | 409 | Création ou réactivation hors horaires |
| `INVALID_TRANSITION` | 409 | Changement de statut interdit |
| `NO_SHOW_TOO_EARLY` | 409 | Délai d'absence pas encore écoulé |
| `QUEUE_EMPTY` | 409 | Aucun ticket à appeler |
| `COUNTER_BUSY` | 409 | Guichet déjà occupé |
| `COUNTER_INACTIVE` | 409 | Guichet inactif |
| `DATABASE_UNAVAILABLE` | 503 | Base injoignable (`/health`) |
| `INTERNAL_ERROR` | 500 | Erreur inattendue |

## Règles appliquées par le backend

- FIFO : le plus ancien ticket `WAITING` du service est appelé en premier.
- Position = 1 + nombre de tickets `WAITING` du même service passés avant lui.
- Notification « rapprochez-vous » dès qu'il reste 10 personnes ou moins devant un ticket en attente.
- Un ticket n'est créé qu'entre l'heure d'ouverture (incluse) et de fermeture (exclue), heure `Africa/Dakar`.
- À la fermeture (ou le lendemain), les tickets `WAITING` et `CALLED` passent à `EXPIRED`.
- Absence : refusée tant que le délai du service n'est pas écoulé ; le ticket suivant est appelé automatiquement.
- Réactivation : seulement depuis `NO_SHOW`, service ouvert, ticket replacé en fin de file.
