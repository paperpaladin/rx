// rx -- a regex engine with a linear-time guarantee.
//
// Parse to a small tree, compile the tree to a program, run the program as a
// Pike VM: all threads advance together over the input, one character at a time,
// and a program counter can appear in the thread list at most once per position.
// That caps the work at O(len(text) * len(program)) and makes catastrophic
// backtracking structurally impossible, rather than merely unlikely.
//
// The classic blowup, (a+)+b against a run of a's with no b, is linear here and
// exponential in any backtracking engine. t/pathological.sh measures it.
#define _POSIX_C_SOURCE 200809L
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

// ------------------------------------------------------------------- the tree

typedef enum { N_CHAR, N_ANY, N_CLASS, N_CAT, N_ALT, N_STAR, N_PLUS, N_QUEST, N_BOL, N_EOL, N_EMPTY } Nk;

typedef struct Node Node;
struct Node {
  Nk k;
  int c;                   // N_CHAR
  unsigned char cls[32];   // N_CLASS bitmap, 256 bits
  Node *l, *r;
};

static const char *pat;    // parse cursor
static const char *pat_start;
static char err[128];

static Node *nd(Nk k) { Node *n = calloc(1, sizeof(Node)); n->k = k; return n; }

static void fail(const char *msg) {
  snprintf(err, sizeof(err), "%s at offset %d", msg, (int)(pat - pat_start));
}
static bool failed(void) { return err[0] != 0; }

static void cls_set(unsigned char *b, int c) { b[(unsigned char)c >> 3] |= 1 << (c & 7); }
static bool cls_get(const unsigned char *b, int c) { return b[(unsigned char)c >> 3] & (1 << (c & 7)); }

// Fills a bitmap for an escape class. Returns false if esc is not a class.
static bool esc_class(int esc, unsigned char *b) {
  switch (esc) {
  case 'd': for (int c = '0'; c <= '9'; c++) cls_set(b, c); return true;
  case 'w':
    for (int c = 'a'; c <= 'z'; c++) cls_set(b, c);
    for (int c = 'A'; c <= 'Z'; c++) cls_set(b, c);
    for (int c = '0'; c <= '9'; c++) cls_set(b, c);
    cls_set(b, '_');
    return true;
  case 's':
    cls_set(b, ' '); cls_set(b, '\t'); cls_set(b, '\n');
    cls_set(b, '\r'); cls_set(b, '\f'); cls_set(b, '\v');
    return true;
  case 'D': case 'W': case 'S': {
    unsigned char inner[32] = {0};
    esc_class(esc + 32, inner);                  // 'D' -> 'd'
    for (int i = 0; i < 32; i++) b[i] = ~inner[i];
    // Match Python: \D, \W, \S do include newline.
    return true;
  }
  default: return false;
  }
}

// Single-character escapes.
static int esc_char(int e) {
  switch (e) {
  case 'n': return '\n';
  case 't': return '\t';
  case 'r': return '\r';
  case 'f': return '\f';
  case 'v': return '\v';
  case '0': return '\0';
  default:  return e;        // \. \\ \* etc are the literal character
  }
}

static Node *parse_alt(void);

// [^a-z0-9\d]
static Node *parse_class(void) {
  Node *n = nd(N_CLASS);
  bool negate = false;
  if (*pat == '^') { negate = true; pat++; }

  bool any = false;
  while (*pat && *pat != ']') {
    int lo;
    if (*pat == '\\') {
      pat++;
      if (!*pat) { fail("trailing backslash in class"); return n; }
      unsigned char sub[32] = {0};
      if (esc_class(*pat, sub)) {
        for (int i = 0; i < 32; i++) n->cls[i] |= sub[i];
        pat++; any = true;
        continue;
      }
      lo = esc_char(*pat++);
    } else {
      lo = (unsigned char)*pat++;
    }

    // A '-' that is not last forms a range.
    if (*pat == '-' && pat[1] && pat[1] != ']') {
      pat++;
      int hi;
      if (*pat == '\\') { pat++; hi = esc_char(*pat++); }
      else hi = (unsigned char)*pat++;
      if (hi < lo) { fail("inverted range in class"); return n; }
      for (int c = lo; c <= hi; c++) cls_set(n->cls, c);
    } else {
      cls_set(n->cls, lo);
    }
    any = true;
  }

  if (*pat != ']') { fail("unterminated ["); return n; }
  pat++;
  if (!any) { fail("empty character class"); return n; }

  if (negate) {
    for (int i = 0; i < 32; i++) n->cls[i] = ~n->cls[i];
    // Python's [^...] does match newline, so we leave it in.
  }
  return n;
}

static Node *parse_atom(void) {
  if (*pat == '(') {
    pat++;
    Node *n = parse_alt();
    if (*pat != ')') { fail("unterminated ("); return n; }
    pat++;
    return n;    // shortcut: no capture groups. Boolean match needs no captures.
  }
  if (*pat == '[') { pat++; return parse_class(); }
  if (*pat == '.') { pat++; return nd(N_ANY); }
  if (*pat == '^') { pat++; return nd(N_BOL); }
  if (*pat == '$') { pat++; return nd(N_EOL); }
  if (*pat == '\\') {
    pat++;
    if (!*pat) { fail("trailing backslash"); return nd(N_EMPTY); }
    unsigned char sub[32] = {0};
    if (esc_class(*pat, sub)) {
      Node *n = nd(N_CLASS);
      memcpy(n->cls, sub, 32);
      pat++;
      return n;
    }
    Node *n = nd(N_CHAR);
    n->c = esc_char(*pat++);
    return n;
  }
  if (*pat == '*' || *pat == '+' || *pat == '?') { fail("nothing to repeat"); return nd(N_EMPTY); }
  if (!*pat || *pat == ')' || *pat == '|') return nd(N_EMPTY);

  Node *n = nd(N_CHAR);
  n->c = (unsigned char)*pat++;
  return n;
}

static Node *parse_repeat(void) {
  Node *n = parse_atom();
  for (;;) {
    Nk k;
    if (*pat == '*') k = N_STAR;
    else if (*pat == '+') k = N_PLUS;
    else if (*pat == '?') k = N_QUEST;
    else return n;
    pat++;
    // A quantifier on an anchor is meaningless; Python allows it, so do we.
    Node *r = nd(k);
    r->l = n;
    n = r;
  }
}

static Node *parse_cat(void) {
  Node *n = NULL;
  while (*pat && *pat != '|' && *pat != ')') {
    Node *r = parse_repeat();
    if (failed()) return n ? n : r;
    if (!n) n = r;
    else { Node *c = nd(N_CAT); c->l = n; c->r = r; n = c; }
  }
  return n ? n : nd(N_EMPTY);
}

static Node *parse_alt(void) {
  Node *n = parse_cat();
  while (*pat == '|') {
    pat++;
    Node *r = parse_cat();
    Node *a = nd(N_ALT); a->l = n; a->r = r;
    n = a;
  }
  return n;
}

// ---------------------------------------------------------------- the program

// I_ANYNL exists only for the implicit unanchored prefix, which must be able to
// skip over newlines even though a user's '.' may not.
typedef enum { I_CHAR, I_ANY, I_ANYNL, I_CLASS, I_SPLIT, I_JMP, I_BOL, I_EOL, I_MATCH } Op;

typedef struct {
  Op op;
  int c;
  int x, y;                 // I_SPLIT / I_JMP targets
  unsigned char cls[32];
} Inst;

static Inst *prog;
static int nprog, prog_cap;

static int emit(Op op) {
  if (nprog == prog_cap) {
    prog_cap = prog_cap ? prog_cap * 2 : 64;
    prog = realloc(prog, prog_cap * sizeof(Inst));
  }
  memset(&prog[nprog], 0, sizeof(Inst));
  prog[nprog].op = op;
  return nprog++;
}

// Emitting from a tree (rather than patching a flat stream as we parse) is what
// makes the quantifiers easy: we already know the whole subexpression.
static void compile(Node *n) {
  switch (n->k) {
  case N_EMPTY: return;
  case N_CHAR:  { int i = emit(I_CHAR);  prog[i].c = n->c; return; }
  case N_ANY:   emit(I_ANY); return;
  case N_CLASS: { int i = emit(I_CLASS); memcpy(prog[i].cls, n->cls, 32); return; }
  case N_BOL:   emit(I_BOL); return;
  case N_EOL:   emit(I_EOL); return;
  case N_CAT:   compile(n->l); compile(n->r); return;
  case N_ALT: {
    int sp = emit(I_SPLIT);
    prog[sp].x = nprog;
    compile(n->l);
    int jp = emit(I_JMP);
    prog[sp].y = nprog;
    compile(n->r);
    prog[jp].x = nprog;
    return;
  }
  case N_STAR: {
    int top = emit(I_SPLIT);
    prog[top].x = nprog;
    compile(n->l);
    int jp = emit(I_JMP);
    prog[jp].x = top;
    prog[top].y = nprog;
    return;
  }
  case N_PLUS: {
    int body = nprog;
    compile(n->l);
    int sp = emit(I_SPLIT);
    prog[sp].x = body;
    prog[sp].y = nprog;
    return;
  }
  case N_QUEST: {
    int sp = emit(I_SPLIT);
    prog[sp].x = nprog;
    compile(n->l);
    prog[sp].y = nprog;
    return;
  }
  }
}

// ------------------------------------------------------------------ the VM

typedef struct { int *pc; int n; } List;

static List clist, nlist;
static int *last_gen;      // last generation a pc was added in
static int gen;

// Adds pc and follows every epsilon transition from it. The last_gen check is
// the linear-time guarantee: each pc enters the list at most once per position.
static void addthread(List *l, int pc, const char *sp, const char *text) {
  if (last_gen[pc] == gen) return;
  last_gen[pc] = gen;

  switch (prog[pc].op) {
  case I_JMP:
    addthread(l, prog[pc].x, sp, text);
    return;
  case I_SPLIT:
    addthread(l, prog[pc].x, sp, text);
    addthread(l, prog[pc].y, sp, text);
    return;
  case I_BOL:
    if (sp == text) addthread(l, pc + 1, sp, text);
    return;
  case I_EOL:
    // Python's $ also matches before a trailing newline; we deliberately do not,
    // and the fuzzer keeps newlines out of its inputs so the two agree.
    if (*sp == 0) addthread(l, pc + 1, sp, text);
    return;
  default:
    l->pc[l->n++] = pc;
    return;
  }
}

// Search semantics: true if the pattern matches anywhere in text.
//
// One generation per thread list, bumped before each list is built. Injecting a
// start thread into clist mid-loop instead would collide with the marks clist
// was already built under, and that dedup is the linear-time guarantee -- so the
// unanchored prefix lives in the program (see rx_compile) rather than here.
static bool rx_search(const char *text) {
  gen = 0;
  memset(last_gen, -1, nprog * sizeof(int));
  clist.n = nlist.n = 0;

  gen++;
  addthread(&clist, 0, text, text);

  for (const char *sp = text; ; sp++) {
    nlist.n = 0;
    gen++;

    for (int i = 0; i < clist.n; i++) {
      Inst *in = &prog[clist.pc[i]];
      if (in->op == I_MATCH) return true;

      bool advance = false;
      switch (in->op) {
      case I_CHAR:  advance = (*sp && (unsigned char)*sp == (unsigned char)in->c); break;
      case I_ANY:   advance = (*sp && *sp != '\n'); break;   // Python: . excludes \n
      case I_ANYNL: advance = (*sp != 0); break;
      case I_CLASS: advance = (*sp && cls_get(in->cls, (unsigned char)*sp)); break;
      default: break;
      }
      if (advance) addthread(&nlist, clist.pc[i] + 1, sp + 1, text);
    }

    if (!*sp) return false;              // ran out of input with no I_MATCH
    List t = clist; clist = nlist; nlist = t;
  }
}

static bool rx_compile(const char *p) {
  err[0] = 0;
  pat = pat_start = p;
  nprog = 0;

  // Implicit unanchored prefix:  0: SPLIT 1, user   1: ANYNL   2: JMP 0
  // '^' still works, because I_BOL rejects any thread whose position moved.
  int sp0 = emit(I_SPLIT);
  prog[sp0].x = nprog;
  emit(I_ANYNL);
  int jp = emit(I_JMP);
  prog[jp].x = sp0;
  prog[sp0].y = nprog;

  Node *tree = parse_alt();
  if (failed()) return false;
  if (*pat) { fail("unexpected )"); return false; }

  compile(tree);
  emit(I_MATCH);

  clist.pc = malloc(nprog * sizeof(int));
  nlist.pc = malloc(nprog * sizeof(int));
  last_gen = malloc(nprog * sizeof(int));
  return true;
}

// ---------------------------------------------------------------------- CLI

int main(int argc, char **argv) {
  if (argc < 2) {
    fprintf(stderr, "usage: rx PATTERN [TEXT]      exit 0 if it matches\n"
                    "       rx -g PATTERN < FILE   print matching lines\n");
    return 2;
  }

  bool grep = !strcmp(argv[1], "-g");
  const char *p = grep ? argv[2] : argv[1];

  if (!rx_compile(p)) {
    fprintf(stderr, "rx: bad pattern: %s\n", err);
    return 2;
  }

  if (grep) {
    char *line = NULL; size_t cap = 0; ssize_t n;
    int found = 1;
    while ((n = getline(&line, &cap, stdin)) > 0) {
      if (line[n - 1] == '\n') line[n - 1] = 0;
      if (rx_search(line)) { puts(line); found = 0; }
    }
    return found;
  }

  if (argc < 3) { fprintf(stderr, "rx: no text given\n"); return 2; }
  return rx_search(argv[2]) ? 0 : 1;
}
