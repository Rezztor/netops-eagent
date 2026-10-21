# Rezztor EAgent base image.
#
# Debian, not Kali. Upstream built on kalilinux/kali-rolling, which ships an
# offensive security toolchain: a scanner in an enterprise registry will flag it,
# and none of it is used by the application. Debian stable also gives predictable
# package versions, where a rolling distribution can change under a rebuild.
#
# Everything installed below is something the application or the agent actually
# needs. openssh-server is deliberately absent -- code execution happens in
# docker/runner, and this container no longer accepts shell connections.
FROM debian:stable-slim


# Set locale to en_US.UTF-8 and timezone to UTC
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y locales tzdata
RUN sed -i -e 's/# \(en_US\.UTF-8 .*\)/\1/' /etc/locale.gen && \
    dpkg-reconfigure --frontend=noninteractive locales && \
    update-locale LANG=en_US.UTF-8 LANGUAGE=en_US:en LC_ALL=en_US.UTF-8
RUN ln -sf /usr/share/zoneinfo/UTC /etc/localtime
RUN echo "UTC" > /etc/timezone
RUN dpkg-reconfigure -f noninteractive tzdata
ENV LANG=en_US.UTF-8
ENV LANGUAGE=en_US:en
ENV LC_ALL=en_US.UTF-8
ENV TZ=UTC

# Copy contents of the project to /
COPY ./fs/ /

# Package installs stay split so an edit to one layer does not rebuild the rest.
RUN bash /ins/install_base_packages1.sh
RUN bash /ins/install_base_packages2.sh
RUN bash /ins/install_base_packages3.sh
RUN bash /ins/install_base_packages4.sh

# install python after packages to ensure version overriding
RUN bash /ins/install_python.sh


# configure ssh
# configure_ssh.sh removed: it enabled PermitRootLogin, and the application
# container no longer runs a shell server at all.

# after install
RUN bash /ins/after_install.sh

# Keep container running infinitely
CMD ["tail", "-f", "/dev/null"]
