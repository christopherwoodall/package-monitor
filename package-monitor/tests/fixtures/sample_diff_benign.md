# Diff Report: safe-utils 3.1.0 → 3.2.0

**Old SHA-256:** `deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef`
**New SHA-256:** `cafebabecafebabecafebabecafebabecafebabecafebabecafebabecafebabe`

| Metric | Count |
|--------|-------|
| Added files | 0 |
| Deleted files | 0 |
| Changed files | 2 |
| Unchanged files | 5 |

## Changed Files

### `package.json`

```diff
--- a/package.json
+++ b/package.json
@@ -1,6 +1,6 @@
 {
   "name": "safe-utils",
-  "version": "3.1.0",
+  "version": "3.2.0",
   "description": "Safe string utility helpers",
   "main": "index.js",
   "scripts": {
```

### `index.js`

```diff
--- a/index.js
+++ b/index.js
@@ -12,3 +12,14 @@
 function capitalize(str) {
   return str.charAt(0).toUpperCase() + str.slice(1);
 }
+
+/**
+ * Truncate a string to maxLen characters, appending ellipsis if needed.
+ * @param {string} str
+ * @param {number} maxLen
+ * @returns {string}
+ */
+function truncate(str, maxLen) {
+  if (str.length <= maxLen) return str;
+  return str.slice(0, maxLen - 3) + '...';
+}
+
+module.exports = { capitalize, truncate };
```
