#!/bin/bash
source /home/jorge/El-Industrial/.env

# IDs de los sitios obtenidos de la API anteriormente
SITE_ID_QA="9eeb459c-4be8-4a45-aa9b-5b168de5075e"
SITE_ID_PRD="f52c7107-70ea-4b80-8645-9dac737a73ec"

check_site() {
    local SITE_ID=$1
    local NAME=$2
    echo "--- Entorno: $NAME ---"
    RESPONSE=$(curl -s -H "Authorization: Bearer $NETLIFY_AUTH_TOKEN" "https://api.netlify.com/api/v1/sites/$SITE_ID")
    
    STATE=$(echo $RESPONSE | jq -r '.state')
    URL=$(echo $RESPONSE | jq -r '.ssl_url')
    UPDATED=$(echo $RESPONSE | jq -r '.updated_at')
    BRANCH=$(echo $RESPONSE | jq -r '.build_settings.repo_branch')
    
    echo "Estado: [$STATE]"
    echo "URL: $URL"
    echo "Rama: $BRANCH"
    echo "Última Actividad: $UPDATED"
    echo ""
}

echo "📊 ESTADO DE INFRAESTRUCTURA NETLIFY"
echo "===================================="
check_site "$SITE_ID_QA" "QA (Dev)"
check_site "$SITE_ID_PRD" "PRODUCCIÓN (Master)"
