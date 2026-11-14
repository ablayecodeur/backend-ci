// CI backend SamaFile : tests Pytest, scans Python, image Docker, Trivy, Docker Hub.
// Job Jenkins depuis la racine du monorepo : script path backend-ci/Jenkinsfile.
// Credential : dockerhub-credentials (utilisateur + jeton Docker Hub).

pipeline {
    agent any

    options {
        timestamps()
        disableConcurrentBuilds()
    }

    parameters {
        booleanParam(name: 'PUBLISH', defaultValue: false, description: 'Publier l image sur Docker Hub après les scans')
    }

    environment {
        LOCAL_IMAGE = 'samafile-backend:ci'
        TRIVY_IMAGE = 'aquasec/trivy:0.58.1'
    }

    stages {
        stage('Préparation') {
            steps {
                dir('backend-ci') {
                    sh 'mkdir -p reports'
                }
            }
        }

        stage('Tests Pytest') {
            steps {
                dir('backend-ci') {
                    sh '''
                    set -eu
                    docker network create "samafile-ci-${BUILD_NUMBER}"
                    docker rm -f "samafile-pg-${BUILD_NUMBER}" >/dev/null 2>&1 || true
                    docker run -d --name "samafile-pg-${BUILD_NUMBER}" \
                        --network "samafile-ci-${BUILD_NUMBER}" \
                        -e POSTGRES_USER=samafile \
                        -e POSTGRES_PASSWORD=ci_only \
                        -e POSTGRES_DB=samafile_test \
                        postgres:16-alpine
                    ready=0
                    for _ in $(seq 1 30); do
                        if docker exec "samafile-pg-${BUILD_NUMBER}" pg_isready -U samafile -d samafile_test; then
                            ready=1
                            break
                        fi
                        sleep 2
                    done
                    test "$ready" = 1
                    docker run --rm \
                        --network "samafile-ci-${BUILD_NUMBER}" \
                        -v "$PWD":/app -w /app \
                        -e APP_ENV=test \
                        -e TEST_DATABASE_ALLOW_REMOTE=1 \
                        -e SECRET_KEY=ci-secret-key-not-for-production-use \
                        -e "TEST_DATABASE_URL=postgresql+psycopg://samafile:ci_only@samafile-pg-${BUILD_NUMBER}:5432/samafile_test" \
                        python:3.12-slim \
                        bash -lc 'pip install --no-cache-dir -r requirements.txt -r tests/requirements.txt && python -m pytest tests --junitxml=reports/pytest.xml'
                    '''
                }
            }
        }

        stage('SAST Bandit') {
            steps {
                dir('backend-ci') {
                    sh '''
                    set -eu
                    docker run --rm -v "$PWD":/app -w /app python:3.12-slim \
                        bash -lc 'pip install --no-cache-dir -r requirements-ci.txt && bandit -r src -ll -f json -o reports/bandit.json && bandit -r src -ll'
                    '''
                }
            }
        }

        stage('Dépendances Python') {
            steps {
                dir('backend-ci') {
                    sh '''
                    set -eu
                    docker run --rm -v "$PWD":/app -w /app python:3.12-slim \
                        bash -lc 'pip install --no-cache-dir -r requirements-ci.txt && pip-audit -r requirements.txt --progress-spinner off --format json --output reports/pip-audit.json && pip-audit -r requirements.txt --progress-spinner off'
                    '''
                }
            }
        }

        stage('Hadolint') {
            steps {
                dir('backend-ci') {
                    sh '''
                    set -eu
                    docker run --rm -i hadolint/hadolint:v2.12.0-alpine --failure-threshold error - < Dockerfile
                    '''
                }
            }
        }

        stage('Image Docker') {
            steps {
                dir('backend-ci') {
                    sh 'docker build -t "$LOCAL_IMAGE" .'
                }
            }
        }

        stage('Trivy image et sources') {
            steps {
                dir('backend-ci') {
                    sh '''
                    set -eu
                    run_trivy() {
                        set +e
                        docker run --rm "$@"
                        code=$?
                        set -e
                        return "$code"
                    }
                    set +e
                    run_trivy \
                        -v /var/run/docker.sock:/var/run/docker.sock \
                        -v "$PWD:/src" \
                        -v "$PWD/reports:/reports" \
                        "$TRIVY_IMAGE" image \
                        --severity HIGH,CRITICAL --ignore-unfixed --exit-code 1 \
                        --format table --output /reports/trivy-image.txt \
                        "$LOCAL_IMAGE"
                    image_code=$?
                    set -e
                    echo "----- Trivy image -----"
                    cat reports/trivy-image.txt || true
                    set +e
                    run_trivy \
                        -v "$PWD:/src" \
                        -v "$PWD/reports:/reports" \
                        "$TRIVY_IMAGE" fs \
                        --scanners vuln,secret,misconfig \
                        --severity HIGH,CRITICAL --ignore-unfixed --exit-code 1 \
                        --skip-dirs /src/.git \
                        --format table --output /reports/trivy-fs.txt \
                        /src
                    fs_code=$?
                    set -e
                    echo "----- Trivy fs -----"
                    cat reports/trivy-fs.txt || true
                    test "$image_code" = 0
                    test "$fs_code" = 0
                    '''
                }
            }
        }

        stage('Publication Docker Hub') {
            when {
                expression { return params.PUBLISH }
            }
            steps {
                withCredentials([usernamePassword(
                    credentialsId: 'dockerhub-credentials',
                    usernameVariable: 'DOCKERHUB_USER',
                    passwordVariable: 'DOCKERHUB_TOKEN'
                )]) {
                    dir('backend-ci') {
                        sh '''
                        set -eu
                        echo "$DOCKERHUB_TOKEN" | docker login -u "$DOCKERHUB_USER" --password-stdin
                        image="${DOCKERHUB_USER}/samafile-backend"
                        docker tag "$LOCAL_IMAGE" "${image}:${BUILD_NUMBER}"
                        docker push "${image}:${BUILD_NUMBER}"
                        sha="$(git rev-parse --short HEAD 2>/dev/null || true)"
                        if [ -n "$sha" ]; then
                            docker tag "$LOCAL_IMAGE" "${image}:${sha}"
                            docker push "${image}:${sha}"
                        fi
                        branch="${BRANCH_NAME:-${GIT_BRANCH:-}}"
                        if printf '%s' "$branch" | grep -Eq '(^|/)(main|master|develop)$'; then
                            docker tag "$LOCAL_IMAGE" "${image}:latest"
                            docker push "${image}:latest"
                        fi
                        docker logout
                        '''
                    }
                }
            }
        }
    }

    post {
        always {
            sh '''
                docker rm -f "samafile-pg-${BUILD_NUMBER}" >/dev/null 2>&1 || true
                docker network rm "samafile-ci-${BUILD_NUMBER}" >/dev/null 2>&1 || true
            '''
            junit allowEmptyResults: true, testResults: 'backend-ci/reports/pytest.xml'
            archiveArtifacts artifacts: 'backend-ci/reports/**', allowEmptyArchive: true
        }
    }
}
