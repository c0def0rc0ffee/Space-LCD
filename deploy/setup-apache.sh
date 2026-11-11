#!/usr/bin/env bash
# <summary>
# One time Apache setup for Space LCD, from the project template. Idempotent, run with
# sudo: sudo ./deploy/setup-apache.sh
# </summary>
# <remarks>
# Unused by this project: nothing is served, and build-zip.sh has APACHE_NAME empty, so
# this is never run. Kept only until its removal is decided (see the packaging notes). It would
# create the docroot owned by the build user so later builds could mirror without sudo.
# </remarks>
set -euo pipefail

name="space-lcd"
owner="rob"
root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

[ "$(id -u)" = 0 ] || { echo "run with sudo" >&2; exit 1; }

install -d -o "$owner" -g www-data -m 2775 "/var/www/html/$name"
echo "docroot ready: /var/www/html/$name"

install -m 644 "$root/vhost.conf" "/etc/apache2/sites-available/$name.conf"
a2ensite "$name.conf" >/dev/null
a2enmod headers rewrite >/dev/null
echo "vhost installed and modules enabled"

grep -q "[[:space:]]$name\$" /etc/hosts || echo "127.0.0.1 $name" >> /etc/hosts
echo "hosts entry present"

# Check the PHP extensions this project needs. Add to the list as required.
for ext in mbstring; do
    php -m 2>/dev/null | grep -qi "^$ext$" || echo "WARNING: PHP extension missing: $ext"
done

apache2ctl -t && systemctl reload apache2
echo "Apache reloaded. Site: http://$name/"
