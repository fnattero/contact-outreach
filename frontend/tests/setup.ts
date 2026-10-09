import "@testing-library/jest-dom/vitest";
import { act, cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Vitest runs without globals, so Testing Library cannot register its own cleanup; without this
// every test would render on top of the previous one. Pending React and animation work is let to
// finish first: a test that ends right after its last assertion otherwise leaves scheduler tasks
// that fire after jsdom is gone ("window is not defined") and fail the whole run at random.
afterEach(async () => {
  if (typeof window !== "undefined") {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
  }
  cleanup();
  // Unmounting queues its own passive-effect work; let that run too before the next test or exit.
  if (typeof window !== "undefined") await new Promise((resolve) => setTimeout(resolve, 0));
});

// Server-only tests (route handlers) run in the node environment, which has no window.
if (typeof window !== "undefined") {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: (query: string): MediaQueryList => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => undefined,
      removeListener: () => undefined,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      dispatchEvent: () => false,
    }),
  });
}

// jsdom has no ResizeObserver; antd's tables, selects and drawers observe element size.
class ResizeObserverStub {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
}
Object.defineProperty(globalThis, "ResizeObserver", { writable: true, value: ResizeObserverStub });

// jsdom does not implement styles of pseudo-elements and prints a "Not implemented" notice to
// stderr on every call, which antd makes constantly. Ignoring the pseudo-element argument returns
// the element's own style (all jsdom can offer anyway) without the notice.
if (typeof window !== "undefined") {
  const realGetComputedStyle = window.getComputedStyle.bind(window);
  window.getComputedStyle = (element: Element) => realGetComputedStyle(element);
}

// Clicking a real link makes jsdom print "Not implemented: navigation". Cancelling the default
// action after React has handled the click keeps the notice away without changing what a test sees.
if (typeof document !== "undefined") {
  document.addEventListener("click", (event) => {
    if ((event.target as Element | null)?.closest?.("a[href]")) event.preventDefault();
  });
}
