/** Independent provider using only the public context and console.http.handler contract. */
export default {
  activate(ctx) {
    ctx.provide('console.http.handler', {
      async handle(_request, response) {
        response.writeHead(503, { 'content-type': 'text/plain; charset=utf-8', 'cache-control': 'no-store' });
        response.end('Console is in explicitly selected maintenance mode. No deployment operation was executed.\n');
      },
    });
  },
};
