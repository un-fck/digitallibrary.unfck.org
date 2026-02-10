export function Footer() {
  return (
    <footer className="border-t border-gray-200 bg-white py-6">
      <div className="mx-auto max-w-7xl px-3 sm:px-4">
        <p className="text-center text-xs text-gray-400">
          © {new Date().getFullYear()} United Nations. All rights reserved.
        </p>
      </div>
    </footer>
  );
}
