import js from "@eslint/js";
import tseslint from "typescript-eslint";
import reactHooks from "eslint-plugin-react-hooks";

export default tseslint.config(
  { ignores: ["dist", "node_modules"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["**/*.{ts,tsx}"],
    plugins: { "react-hooks": reactHooks },
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: {
        window: "readonly",
        document: "readonly",
        fetch: "readonly",
        localStorage: "readonly",
        AbortController: "readonly",
        console: "readonly",
      },
    },
    rules: {
      "@typescript-eslint/consistent-type-imports": "error",
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
      // The API returns `unknown` bodies on purpose; narrowing is the caller's
      // job, and `any` would quietly defeat that.
      "@typescript-eslint/no-explicit-any": "error",
      eqeqeq: ["error", "smart"],
      ...reactHooks.configs.recommended.rules,
      // Data loading here is the plain effect pattern: start a request, set
      // state when it resolves, guard against a resolved response from an
      // unmounted or superseded render. This rule targets synchronous setState
      // during an effect, which is a real problem; setting state from an async
      // callback afterwards is the pattern working as intended. The alternative
      // is a data-fetching dependency, which this project deliberately does not
      // carry -- see src/lib/useAsync.ts.
      "react-hooks/set-state-in-effect": "off",
    },
  },
);
