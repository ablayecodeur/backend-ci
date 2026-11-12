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

La connexion PostgreSQL est fournie exclusivement par la variable `DATABASE_URL`. Copiez `.env.example` vers `.env`, puis adaptez l'hôte, le nom d'utilisateur et le mot de passe. Aucun identifiant de base réel ne doit être commité.

Les schémas sont gérés par **Alembic**. Le conteneur exécute `alembic upgrade head` avant de démarrer l'API. La migration initiale est conçue pour créer les tables sur une base neuve et compléter les colonnes manquantes sur une base existante.

Créer les bases PostgreSQL une seule fois (pgAdmin, Query Tool, une commande à la fois) :

```sql
CREATE ROLE samafile LOGIN PASSWORD 'CHANGE_ME';
CREATE DATABASE samafile OWNER samafile;
CREATE DATABASE samafile_test OWNER samafile;
```

## Lancer le backend dans Docker

Le backend doit être sur le même réseau Docker que PostgreSQL. Copiez et complétez le fichier d'environnement :

```bash
cp .env.example .env
# Modifier DATABASE_URL, SECRET_KEY, ADMIN_PASSWORD et CORS_ORIGINS
docker network create samafile
docker network connect samafile postgres_server

docker build -t samafile/backend .
docker run -d --name backend --network samafile --env-file .env -p 8000:8000 samafile/backend
```

Pour Docker Compose, adaptez l'hôte de `DATABASE_URL` au nom du service PostgreSQL (souvent `db`). En production, définir `APP_ENV=production`, une clé `SECRET_KEY` forte et les véritables origines frontend dans `CORS_ORIGINS`.

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
| CORS | Origines autorisées configurées par `CORS_ORIGINS` |
| Identifiants | Entiers (`id: 25`) |
| Authentification | JWT : en-tête `Authorization: Bearer <jeton>` (voir « Comptes, rôles et sécurité ») |
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
  "ticket_cutoff_time": null,
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
- Un ticket n'est créé qu'entre l'ouverture (incluse) et `ticket_cutoff_time` (exclue) si configurée, sinon jusqu'à la fermeture (exclue), heure `Africa/Dakar`.
- À la fermeture (ou le lendemain), les tickets `WAITING`, `CALLED` et `IN_PROGRESS` passent à `EXPIRED`.
- Absence : refusée tant que le délai du service n'est pas écoulé ; le ticket suivant est appelé automatiquement.
- Réactivation : seulement depuis `NO_SHOW`, service ouvert, ticket replacé en fin de file.


---

# Comptes, rôles et sécurité (version 2)

## Les trois rôles

| Rôle | Qui | Ce qu'il fait |
|---|---|---|
| **USER** | Usager | S'inscrit, se connecte, prend un ticket, suit sa position, annule son ticket, consulte son historique, modifie son profil et son mot de passe. |
| **AGENT** | Agent de guichet (assigné à un guichet) | Crée des comptes usagers et des tickets au comptoir, appelle le suivant **depuis son guichet**, démarre / termine / déclare absent, réactive ou annule les tickets de **son service**, réinitialise un mot de passe usager, consulte les statistiques de son service. |
| **ADMIN** | Administrateur | Tout ce que fait un agent, sur tous les services et guichets, plus : créer agents et administrateurs, affecter un agent à un guichet, activer / désactiver des comptes, créer et modifier services et guichets (horaires, délai d'absence), statistiques globales. |

## Tableau des accès

| Route | Public | USER | AGENT | ADMIN |
|---|:-:|:-:|:-:|:-:|
| `GET /health`, `GET /api/services`, `GET /api/services/{id}`, `GET /api/counters` | oui | oui | oui | oui |
| `POST /api/auth/register`, `POST /api/auth/login` | oui | | | |
| `GET/PATCH /api/auth/me`, `POST /api/auth/change-password` | | oui | oui | oui |
| `POST /api/tickets` | | pour lui-même | son service | tous |
| `GET /api/me/tickets` | | oui | oui | oui |
| `GET /api/tickets/{id}`, `GET /api/tickets/{id}/position`, `PATCH /api/tickets/{id}/cancel` | | ses tickets | son service | tous |
| `GET /api/tickets`, `GET /api/counters/{id}/tickets` | | | son service | tous |
| `POST /api/counters/{id}/next` | | | son guichet | tous |
| `PATCH /api/tickets/{id}/start`, `/complete`, `/no-show` | | | tickets de son guichet | tous |
| `POST /api/tickets/{id}/reactivate` | | | son service | tous |
| `POST /api/users`, `GET /api/users`, `GET/PATCH /api/users/{id}`, `POST /api/users/{id}/reset-password` | | | comptes USER | tous |
| `POST/PATCH /api/services`, `POST/PATCH /api/counters` | | | | oui |
| `GET /api/stats` | | | son service | tous |

## Authentification

### POST /api/auth/register (201)
Inscription libre d'un usager (rôle USER, jamais autre chose).
```json
{"email": "awa@exemple.sn", "password": "8 caractères minimum", "full_name": "Awa Ndiaye", "phone": "771234567"}
```

### POST /api/auth/login (200)
```json
{"email": "awa@exemple.sn", "password": "..."}
```

Réponse de `register` et `login` :
```json
{
  "access_token": "eyJ...",
  "token_type": "bearer",
  "expires_in": 28800,
  "user": {"id": 3, "email": "awa@exemple.sn", "full_name": "Awa Ndiaye", "phone": "771234567",
           "role": "USER", "counter_id": null, "is_active": true}
}
```
Le frontend enregistre `access_token` et l'envoie dans `Authorization: Bearer <access_token>`. Le jeton dure 8 heures. Le champ `user.role` indique quelle interface afficher (usager, guichet agent, administration).

### GET /api/auth/me, PATCH /api/auth/me
`PATCH` accepte `{"full_name": "...", "phone": "..."}`.

### POST /api/auth/change-password
`{"current_password": "...", "new_password": "..."}` → 200, ou 400 `INVALID_PASSWORD`.

## Usager

### POST /api/tickets
`{"service_id": 1}` : `user_name` est facultatif et vaut par défaut le nom du compte. Le ticket est rattaché au compte.

### GET /api/me/tickets
Historique des tickets du compte, du plus récent au plus ancien. Filtres : `status`, `limit`, `offset`. Réponse : `{"items": [...], "total": n}`.

Le suivi de position ne change pas : interroger `GET /api/tickets/{id}/position` toutes les 5 à 10 secondes (le serveur ne pousse pas de notification).

## Agent et administrateur

### POST /api/users (201) : créer un compte
```json
{"email": "client@exemple.sn", "password": "Motdepasse1", "full_name": "Client", "phone": "77...", "role": "USER"}
```
- Un **agent** ne crée que des comptes `USER`.
- Un **administrateur** crée aussi `AGENT` (avec `counter_id` pour l'affecter à un guichet) et `ADMIN`.

### GET /api/users
Filtres : `role`, `q` (recherche nom ou email), `is_active`, `limit`, `offset`. Un agent ne voit que les usagers.

### PATCH /api/users/{id}
Administrateur : `full_name`, `phone`, `role`, `counter_id`, `is_active`. Agent : `full_name`, `phone`, `is_active` d'un usager. Un compte désactivé ne peut plus se connecter et son jeton est refusé. On ne peut pas se désactiver ni changer son propre rôle (409 `CANNOT_MODIFY_SELF`).

### POST /api/users/{id}/reset-password
`{"new_password": "..."}` : définit un nouveau mot de passe (usager qui a oublié le sien).

### Tickets au comptoir
`POST /api/tickets` par un agent ou un administrateur accepte en plus :
- `user_id` : crée le ticket pour ce compte usager (l'usager le retrouve dans `GET /api/me/tickets`) ;
- ou seulement `user_name` : personne sans compte.

### Travail au guichet
Un agent appelle le suivant avec `POST /api/counters/{son_counter_id}/next`, puis `start`, `complete` ou `no-show` sur le ticket appelé à son guichet. Son `counter_id` est dans `GET /api/auth/me`. Sans guichet assigné : 403 `NO_COUNTER_ASSIGNED`.

## Administrateur : services, guichets, statistiques

### POST /api/services (201)
```json
{"name": "Conseil", "opening_time": "08:00", "closing_time": "16:00", "no_show_timeout_seconds": 120}
```
`opening_time` doit précéder `closing_time` (sinon 422). Réponse : le même format que `GET /api/services/{id}`.

### PATCH /api/services/{id}
Champs facultatifs : `name`, `opening_time`, `closing_time`, `ticket_cutoff_time` (`HH:MM`, `null` pour supprimer), `no_show_timeout_seconds`, `is_active` (`false` retire le service de la liste publique).

### POST /api/counters (201), PATCH /api/counters/{id}
`{"name": "Guichet 3", "service_id": 1}` ; `PATCH` : `name`, `is_active`.

### GET /api/stats
Tableau de bord du jour (filtre facultatif `service_id`) :
```json
{
  "date": "2026-11-10",
  "total_tickets": 12,
  "services": [
    {"service_id": 1, "service_name": "Dépôt", "total_today": 12, "waiting": 3, "called": 1, "in_progress": 1,
     "completed": 5, "no_show": 1, "cancelled": 1, "expired": 0, "average_wait_seconds": 240}
  ]
}
```

## Erreurs liées aux comptes

| Statut | Code | Cas |
|---|---|---|
| 401 | `UNAUTHORIZED` | Jeton absent |
| 401 | `INVALID_TOKEN` | Jeton invalide, expiré ou compte désactivé |
| 401 | `INVALID_CREDENTIALS` | Email ou mot de passe incorrect |
| 400 | `INVALID_PASSWORD` | Mot de passe actuel incorrect |
| 403 | `FORBIDDEN` | Rôle insuffisant ou ressource hors périmètre (ticket d'un autre usager, autre service, autre guichet) |
| 403 | `NO_COUNTER_ASSIGNED` | Agent sans guichet |
| 404 | `USER_NOT_FOUND` | Compte introuvable |
| 409 | `EMAIL_ALREADY_USED` | Email déjà inscrit |
| 409 | `CANNOT_MODIFY_SELF` | Auto-désactivation ou changement de son propre rôle |

## Variables d'environnement

| Variable | Rôle |
|---|---|
| `DATABASE_URL` | Connexion PostgreSQL (obligatoire) |
| `APP_ENV` | `production` en déploiement. |
| `SECRET_KEY` | Clé de signature JWT ; obligatoire si `APP_ENV=production`. |
| `CORS_ORIGINS` | Origines frontend autorisées, séparées par des virgules. |
| `ADMIN_EMAIL`, `ADMIN_PASSWORD`, `ADMIN_NAME` | Créent le premier compte administrateur au démarrage s'il n'existe pas. |
| `SEED_DEMO_ACCOUNTS=true` | Crée `agent1@samafile.sn` (guichet 1), `agent2@samafile.sn` (guichet 2) et `usager@samafile.sn`. |
| `DEMO_PASSWORD` | Mot de passe des comptes de démonstration (défaut `Demo12345`). |
| `ACCESS_TOKEN_MINUTES` | Durée de vie du jeton (480 par défaut). |

Exemple de lancement pour le développement et la démonstration :
```bash
docker run -d --name backend_ci --network samafile -p 8000:8000 \
  -e SECRET_KEY=une-longue-valeur-secrete \
  -e ADMIN_EMAIL=admin@samafile.sn -e ADMIN_PASSWORD=UnMotDePasseSolide1 \
  -e SEED_DEMO_ACCOUNTS=true \
  samafile/backend
```

Le schéma est versionné par Alembic et migré avant le démarrage de l'API.

## Affichage du guichet appelant (écran de l'usager)

Dès qu'un guichet appelle un ticket, `GET /api/tickets/{id}` et `GET /api/tickets/{id}/position` renvoient `counter_id` et `counter_name` (par exemple `"Dépôt - guichet 1"`). Le champ `message` devient « Votre ticket est appelé. Présentez-vous au guichet : Dépôt - guichet 1. ». Tant que le ticket est en attente, ces champs valent `null`. Deux guichets n'appellent jamais le même ticket (verrou `FOR UPDATE SKIP LOCKED` en base).
