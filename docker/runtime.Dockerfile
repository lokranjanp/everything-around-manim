FROM manimcommunity/manim:v0.19.0

USER root
COPY docker/runtime-entrypoint.sh /runtime/run.sh
RUN chmod 0555 /runtime/run.sh && mkdir -p /input /output && chmod 0777 /input /output
USER 1000:1000
ENTRYPOINT ["/runtime/run.sh"]

