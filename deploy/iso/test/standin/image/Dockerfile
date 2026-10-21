# A tiny stand-in for every image of the stack, for test-iso.sh on a machine
# where QEMU has no KVM and loading the real 17 GB image would take hours.
# It answers /api/health and serves a page; it is not the product.
FROM alpine:3.22
COPY serve.sh /serve.sh
# The Compose file's health checks call these two names. 8080 is the port the
# application listens on inside its container.
RUN chmod +x /serve.sh \
 && printf '#!/bin/sh\nexec wget -q -O /dev/null http://127.0.0.1:8080/api/health\n' > /usr/local/bin/python3 \
 && printf '#!/bin/sh\nexit 0\n' > /usr/local/bin/pg_isready \
 && chmod +x /usr/local/bin/python3 /usr/local/bin/pg_isready
CMD ["nc", "-lk", "-p", "8080", "-e", "/serve.sh"]
