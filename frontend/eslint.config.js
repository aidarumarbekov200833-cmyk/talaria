// ESLint 9 (flat config). Перенос из .eslintrc.json: старый формат ESLint 9
// не читает («couldn't find eslint.config»), и линтер молча не запускался.
import globals from 'globals';

export default [
  {
    ignores: ['**/*.min.js', 'vendor/**', 'node_modules/**', 'insights/**'],
  },
  {
    files: ['js/**/*.js', 'tests/**/*.js', 'playwright.config.js'],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: 'module',
      globals: { ...globals.browser, ...globals.node },
    },
    rules: {
      'no-unused-vars': [
        'warn',
        { varsIgnorePattern: '^_', argsIgnorePattern: '^_', caughtErrors: 'none' },
      ],
      'no-undef': 'off',
      'no-console': 'off',
      semi: ['warn', 'always'],
      'no-trailing-spaces': 'warn',
      eqeqeq: ['error', 'always', { null: 'ignore' }],
      'no-eval': 'error',
      'no-implied-eval': 'error',
      'no-new-func': 'error',
    },
  },
];
