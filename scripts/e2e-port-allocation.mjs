import { createServer } from "node:net";

export function reserveTcpPort() {
  return new Promise((resolve, reject) => {
    const server = createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      if (!address || typeof address === "string") {
        reject(new Error("could not reserve a TCP port"));
        return;
      }
      server.close((error) => error ? reject(error) : resolve(address.port));
    });
  });
}

function explicitPort(environment, name) {
  const value = environment[name];
  if (value === undefined || value === "") return undefined;
  const port = Number(value);
  if (!Number.isInteger(port) || port < 1 || port > 65_535) {
    throw new Error(`${name} must be a TCP port`);
  }
  return port;
}

/**
 * Respect an explicitly requested endpoint, otherwise ask the OS for each
 * endpoint independently. A duplicate explicit pair is an immediate failure,
 * never an accidental shared API/Web server.
 */
export async function resolveE2ePorts(
  environment = process.env,
  { reserve = reserveTcpPort } = {},
) {
  const explicitApiPort = explicitPort(environment, "PLAYWRIGHT_API_PORT");
  const explicitWebPort = explicitPort(environment, "PLAYWRIGHT_WEB_PORT");
  const [apiPort, webPort] = await Promise.all([
    explicitApiPort ?? reserve(),
    explicitWebPort ?? reserve(),
  ]);
  if (apiPort === webPort) {
    throw new Error("PLAYWRIGHT_API_PORT and PLAYWRIGHT_WEB_PORT must resolve to different ports");
  }
  return { apiPort, webPort };
}
