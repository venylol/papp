/*
 * edmonds.c:
 * - Algorithme d'Edmonds-Johnson pour la recherche d'un couplage de poids maximum dans un graphe.
 * - Edmonds-Johnson algorithm to obtain a maximum weight matching in a graph.
 *
 * - Les notations utilisees sont celles du livre d'Alan Gibbons,
 * - Used notations come from Alan Gibbons' book
 * "Algorithmic Graph Theory", Cambridge University Press (1985), chap. 5, pp 125--152.
 *
 * See also: https://en.wikipedia.org/wiki/Blossom_algorithm
 *
 * (EL) 18/10/2018 : v1.37, English version for code.
 * (EL) 22/09/2012 : v1.36, no change.
 * (EL) 12/09/2012 : v1.35, no change
 * (EL) 16/07/2012 : v1.34, no change
 * (EL) 05/05/2008 : v1.33, Tous les 'int' deviennent 'long' pour etre sur d'etre sur 4 octets.
 * (EL) 21/04/2008 : v1.32, no change
 * (EL) 29/04/2007 : v1.31, no change
 * (EL) 06/04/2007 : changement des 'fopen()' en 'myfopen_in_subfolder()'
 * (EL) 13/01/2007 : v1.30 by E. Lazard, no change
 *
 *********
 *
 * (EL) 02/07/2018 : v1.37, English version for code.
 * (EL) 22/09/2012 : v1.36, no change.
 * (EL) 12/09/2012 : v1.35, no change
 * (EL) 16/07/2012 : v1.34, no change
 * (EL) 05/05/2008 : v1.33, All 'int' become 'long' to force 4 bytes storage.
 * (EL) 21/04/2008 : v1.32, no change
 * (EL) 29/04/2007 : v1.31, no change
 * (EL) 06/04/2007 : change all 'fopen()' to 'myfopen_in_subfolder()'
 * (EL) 13/01/2007 : v1.30 by E. Lazard, no change
 *
 */


/* Interface avec le reste du programme - interface with the rest of the code */

#include <stdio.h>
#include <stdlib.h>
#include <assert.h>
#include <limits.h>
#include "global.h"
#include "pairings.h"
#include "changes.h"

/*
 * Les poids des aretes et les variables y et z sont stockes dans des variables de type "weight".
 * On pourrait utiliser des "long long" (entiers sur 64 bits), mais tous les compilateurs n'ont pas ca,
 * et le GCC ne semble pas aller plus vite qu'avec des "double" (virgule flottante, double precision).
 * On n'utilise donc les "long long" que dans le cas de DJGPP, car l'emulation 387 semble avoir des
 * problemes de precision.
 ****
 * Edge weights and y, z variables are stored in "weight" type variables.
 * "long long" (64 bits integers) could be used but all compilers don't offer them and GCC
 * doesn't seem to run faster than with "double" type. "long long" are therefore only used
 * when DJGPP is ised because 387 emulation seems to have precision issues.
 */

#if defined(__GO32__)
# define USE_LONG_LONG
# define LONG_LONG_MAX  9223372036854775807LL
#endif

#ifndef USE_LONG_LONG
# include <float.h>
# define weight     double
# define pen2weight(x)  ((weight)(x))
# define INFINI     (DBL_MAX/2)
#else
# define weight     long long
# define pen2weight(x)  ((weight)(x) << 32)
# define INFINI     LONG_LONG_MAX
#endif

#define ED_CALLOC(var,size,type)   do {            \
    var=(type*)malloc((size)*sizeof(type)); \
    if (var==NULL) abort();                 \
    } while (0)

/*#define EDEBUG*/

#ifdef EDEBUG
# define TRACE(x)       fprintf x
#else
# define TRACE(x)
#endif

static weight **p;
static long NumberOfVertices, *T;

static void edmonds_init(void), edmonds(void), edmonds_end(void);

/*
 * La fonction doPairings(n, _p, v) prend comme parametres la _moitie_ du
 * nombre de joueurs, l'adresse de la matrice des penalites, et l'adresse
 * d'un tableau de longueur 2*n pour stocker le vecteur resultat, sous
 * la forme (i1 j1 i2 j2 ... in jn), si (i1,j1)...(in,jn) est l'appariement
 * optimal. Le resultat est alors la penalite de l'appariement optimal.
 *
 * L'algorithme utilise dans edmonds() attend, lui, le nombre de joueurs
 * (NumberOfVertices), la matrice symetrisee des poids (et non pas des penalites),
 * et au retour remplit le tableau T sous la forme (T0 T1 ... T2N), ou
 * T[i] est l'adversaire de i. Il faut encore remettre ce resultat en forme
 * pour obtenir v, en particulier determiner dans quel sens les joueurs
 * seront apparies (i.e. qui aura les noirs).
 ****
 * doPairings(n, _p, v) takes asa parameters: half the number of players, penalties
 * matrix address and a 2*n array to save the result vector, which will be
 * (i1 j1 i2 j2 ... in jn) if (i1,j1)...(in,jn) is the optimal matching.
 * Function returns the maximum matching penalty.
 *
 * The algorithm used in edmonds() awaits the numer of players (NumberOfVertices),
 * symmetrical matrix of weights (and not penalties) and return the array T
 * which will look like (T0 T1 ... T2N), where T[i] is i's opponent. Result must
 * still be reshaped to obtain v, in particular to decide in which way players
 * will be paired (ie: which player will be black).
 */

pen_t doPairings(long n, pen_t **_p, long *v) {
    long i, j, k, rounded;
    pen_t total, p1, p2;

#ifdef SAVE_MATRIX
    /*
     * Sauve la matrice dans un fichier qui peut etre relu par 'test'.
     * Ainsi, si une erreur se produit dans ce module, elle peut etre
     * facilement reproduite.
     ****
     * Save matrix in a file which can be reread by 'test'.
     * Therefore, if an error occurs in this module, it can be easily
     * be reproduced.
     */
    FILE *fp = myfopen_in_subfolder("matrix.pen","w", "", 0, 0);
    if (fp) {
        fprintf(fp,"%ld\n\n", n);
        for (i = 0; i < 2*n; i++) {
            for (j = 0; j < 2*n; j++)
                fprintf(fp," %8lu", _p[i][j]);
            fprintf(fp,"\n");
        }
        fclose(fp);
    }
#endif
    NumberOfVertices = 2*n;
    rounded = 0;

    /*
     * Verifier que la precision des calculs est suffisante
     * (il est bien tard pour verifier, mais vieux motard que jamais).
     ****
     * Check precision is large enough.
     */
    assert(sizeof(weight) >= 6);

    /* Rendre la matrice _p symetrique - symmetrise _p matrix */
    ED_CALLOC(p, NumberOfVertices, weight*);
    for (i = 0; i < NumberOfVertices; i++)
        ED_CALLOC(p[i], NumberOfVertices, weight);
    ED_CALLOC(T, NumberOfVertices, long);

    for (i = 0; i < NumberOfVertices; i++)
        for (j = 0; j < i; j++) {
            p1 = _p[i][j];
            p2 = _p[j][i];
            if (p1 > p2)
                p1 = p2;
            if (p1 > MAX_PEN) {
                p1 = MAX_PEN;
                ++rounded;
            }
            if (p1 < 0) {
                p1 = 0;
                ++rounded;
            }
            p[i][j] = p[j][i] = pen2weight(MAX_PEN + 1 - p1);
        }
    if (rounded)
        fprintf(stderr,
#ifdef ENGLISH
        "Warning: %ld penalties have been truncated,\n"
        "since they weren't between 0 and %ld.\n"
#else
        "Attention, %ld penalites ont ete tronquees,\n"
        "c.a.d. ramenees dans l'intervalle 0--%ld.\n"
#endif
        , rounded, MAX_PEN);

    /* Annulons a tout hasard les elements diagonaux - cancel diagonal elements */
    for (i = 0; i < NumberOfVertices; i++)
        p[i][i] = (weight)0;

    edmonds_init();
    edmonds();
    edmonds_end();

    /* Tout le monde devrait etre apparie - All players should be paired */
    for (i = 0; i < NumberOfVertices; i++) {
        assert(T[i] != i && T[T[i]] == i);
        v[i] = i;
    }
    total = (pen_t)0;
    for (i = 0; i < NumberOfVertices; i+= 2) {
        j = T[v[i]];    /* A mettre en position i+1 - to be put in i+1 position */
        for (k = i+2; k < NumberOfVertices; k++)
            if (v[k] == j)
                v[k]=v[i+1], v[i+1]=j;
    /* Il reste a ordonner les paires - Pairs must now be ordered */
        if (_p[v[i]][v[i+1]] > _p[v[i+1]][v[i]])
            k=v[i], v[i]=v[i+1], v[i+1]=k;
        total += _p[v[i]][v[i+1]];
    }
    /* Et a liberer la memoire - and memory must be freed */
    for (i = 0; i < NumberOfVertices; i++)
        free(p[i]);
    free(p);
    free(T);

#ifdef SAVE_MATRIX
    fp = myfopen_in_subfolder("matrix.out","w", "", 0, 0);
    if (fp) {
        for (i=0; i<n; i++) {
            j = v[2*i];
            k = v[2*i+1];
            fprintf(fp, "%ld-%ld\t%lu\n", j, k, _p[j][k]);
        }
        fprintf(fp,"\nTotal =\t%lu\n", total);
        fclose(fp);
    }
#endif
    return total;
}

/*
 * Structures de donnees utilisees - Used data structures
 */

#define UNLABELLED      0
#define LBL_INNER       1
#define LBL_OUTER       2

typedef struct _pseudo_vertex {
    long cardinal;
    long label;
    weight z;
    struct _pseudo_vertex *next;
    struct _pseudo_vertex *parent;
    struct _pseudo_vertex *oldest;
    struct _pseudo_vertex *partner;
    struct _pseudo_vertex *to_root;
    struct _pseudo_vertex **neighbours;
} pseudo_vertex;

typedef struct _vertex {
    weight y;
    pseudo_vertex *interne, *externe;
} vertex;

static pseudo_vertex *pseudo_vertices_list;
static vertex *vertices_list;
static long **e_star;

/*
 * Cree un pseudo_vertex, sans le lier aux autres.
 ****
 * Creates a pseudo-vertex, without linking it to others.
 */

static pseudo_vertex *create_pseudo_vertex(long cardinal, long label) {
    pseudo_vertex *pv;

    ED_CALLOC(pv, 1, pseudo_vertex);
    pv->cardinal = cardinal;
    pv->label = label;
    pv->z = (weight)0;
    pv->parent = pv->oldest = pv->partner = pv->to_root = NULL;
    ED_CALLOC(pv->neighbours, NumberOfVertices, pseudo_vertex*);
    pv->neighbours[0] = NULL;
    return pv;
}

/*
 * Detruit un pseudo-sommet, sans le delier des autres
 ****
 * Destroys a pseudo-vertex, without unlinking it from others
 */

static void destroy_pseudo_vertex(pseudo_vertex *pv) {
    assert(pv);
    assert(pv->neighbours);
    free(pv->neighbours);
    free(pv);
}

/*
 * Initialisation des structures de donnees. Nous affichons une "regle" de
 * [NumberOfVertices/2] signes "-" entre crochets; c'est le nombre maximum
 * d'augmentations fortes qui peuvent avoir lieu. Pour nous, le maximum
 * sera toujours atteint (tout couplage optimal est parfait).
 ****
 * Data structures initialization. We display a "ruler" of [NumberOfVertices/2]
 * '-' signs between brackets; it's the maximum of strong augmentations that
 * can happen. For us, maximum will always be attained (any optimal matching is
 * perfect).
 */

static void edmonds_init (void) {
    long i;
    vertex *v;
    pseudo_vertex *pv;

#ifndef EDEBUG
    if (!papp_tournament_api) {
        fprintf(stderr, "[");
        for (i = 0; i < NumberOfVertices/2; i++)
            fprintf(stderr, "-");
        fprintf(stderr, "]\r[");
    }
#endif

    /* D'abord initialiser la liste des sommets - First initialize list of vertices */
    pseudo_vertices_list = NULL;
    ED_CALLOC(vertices_list, NumberOfVertices, vertex);
    for (i = 0; i < NumberOfVertices; i++) {
        pv = create_pseudo_vertex(1, UNLABELLED);
        v = vertices_list + i;
        v->y = (weight)0;
        v->interne = v->externe = pv;
        pv->next = pseudo_vertices_list;
        pseudo_vertices_list = pv;
    }
    /* Puis allouer de la memoire pour le graphe G' - allocate memory for graph G' */
    ED_CALLOC(e_star, NumberOfVertices, long*);
    for (i = 0; i < NumberOfVertices; i++)
        ED_CALLOC(e_star[i], NumberOfVertices, long);
}

/*
 * Liberation de ces memes structures de donnees. A la fin de l'execution  de l'algorithme,
 * il ne doit pas rester de blossoms (il y a donc exactement autant de pseudo-sommets que de sommets)
 ****
 * Freeing these same data structures. At the end of the algorithm execution, no blossoms
 * must be left (there are exactly as many pseudo-vertices as vertices).
 */

static void edmonds_end (void) {
    long i;
    vertex *v;
    pseudo_vertex *pv;

#ifndef EDEBUG
    if (!papp_tournament_api) fprintf(stderr, "\n");
#endif

    for (i = 0; i < NumberOfVertices; i++)
        free(e_star[i]);
    free(e_star);

    /* Verifier le nombre de pseudo-sommets */
    for (i=0, pv=pseudo_vertices_list; pv; pv=pv->next) {
        assert(pv->cardinal == 1);
        ++i;
    }
    assert(i == NumberOfVertices);

    for (i = 0; i < NumberOfVertices; i++) {
        v = vertices_list + i;
        pv = v->interne;
        assert(pv); assert(pv == v->externe);
        destroy_pseudo_vertex(pv);
    }
    free(vertices_list);
}

/*
 * Ajoute v a la liste des voisins de u. Renvoie 1 en cas de succes, sinon 0
 * (si v etait deja voisin de u)
 ****
 * Add v to the list of neighbors of u. Returns 1 if success, 0 otherwise
 * (if v is already a neighbor of u).
 */

static long add_neighbor(pseudo_vertex *u, pseudo_vertex *v) {
    pseudo_vertex **u1;

    assert(u); assert(v);
    for (u1 = u->neighbours; *u1; ++u1)
        if (*u1 == v)
            return 0;
    *u1++ = v;
    *u1 = NULL;
    return 1;
}

#ifdef EDEBUG
/*
 * Verifie que deux pseudo-sommets sont voisins - Check if two pseudo-vertices are neighbors
 */
static long sont_voisins(pseudo_vertex *u, pseudo_vertex *v) {
    long i, j;

    for (i = 0; i < NumberOfVertices; i++)
        if (vertices_list[i].externe == u)
        for (j = 0; j < NumberOfVertices; j++)
            if (vertices_list[j].externe == v && e_star[i][j])
            return 1;
    return 0;
}
#endif

/*
 * Mise a jour des voisins, apres la contraction ou l'expansion
 * d'un blossom, ou apres augmentation de E*
 ****
 * Update neighbors, after contraction or expansion of a blossom,
 * or after augmentation of E*
 */

static void recompute_neighbors(void) {
    pseudo_vertex *t, *v;
    long i, j;

    /* D'abord purger toutes les listes de voisins - first clean all neighbors list */
    for (t = pseudo_vertices_list; t; t = t->next)
        t->neighbours[0] = NULL;

    for (i = 0; i < NumberOfVertices; i++) {
        t = vertices_list[i].externe;
        for (j = 0; j < NumberOfVertices; j++)
            if ((v = vertices_list[j].externe)!=t && e_star[i][j])
                add_neighbor(t, v);
    }
}

/*
 * Mise a jour des pseudo-sommets externes, i.e. determiner quel est le
 * pseudo-sommet le plus externe contenant un sommet donne.
 ****
 * Update external pseudo-vertices, ie: find which is the most external
 * pseudo-vertex containing a given vertex.
 */

static void recompute_external_pv(void) {
    long i;
    vertex *v;
    pseudo_vertex *pv;

    for (i = 0; i < NumberOfVertices; i++) {
        v = vertices_list + i;
        for (pv = v->interne; pv->parent; )
            pv = pv->parent;
        v->externe = pv;
    }
}

/*
 * Expansion d'un blossom externe, en induisant un couplage maximum parmi
 * les pseudo-sommets ainsi liberes.
 ****
 * Expansion of an external blossom, giving a maximum matching for freed
 * pseudo-vertices
 */

static void expand_blossom(pseudo_vertex *u) {
    pseudo_vertex **old, *t, *c, *ia, *ib, *t1;
    long i, j;

    TRACE((stderr, "Expansion d'un blossom [\n"));
    /* Verifier que le sommet est bien externe - Check vertex is external */
    assert(u); assert(u->cardinal > 1 && u->cardinal % 2);
    assert(u->parent == NULL); assert(u->oldest);

    TRACE((stderr, "Recherche d'un partner\n"));
    ia = u->oldest;
    ia->partner = NULL;
    /* Avons-nous un partner ? - Do we have a partner? */
    if ((c = u->partner) != NULL) {
        assert(c->partner == u);
        assert((c->label == LBL_OUTER  && u->label == LBL_INNER)
            || (c->label == UNLABELLED && u->label == UNLABELLED));
        /* Lequel de nos enfants va recuperer le partner ? - Which of our children will get the partner? */
        for (i = 0; i < NumberOfVertices; i++)
            if (vertices_list[i].externe == c)
                for (j = 0; j < NumberOfVertices; j++)
                    if (vertices_list[j].externe ==u && e_star[i][j]) {
                        /* Trouver le fils - Find the son */
                        for (t = vertices_list[j].interne;
                            t && t->parent != u; t = t->parent)  ;
                        assert(t->parent == u);
                        t->partner = c;
                        c->partner = ia = t;
                        if (c->to_root == u)
                            c->to_root = ia;
                        goto suite;
                    }
        assert(0);  /* le partner est introuvable?? - Partner cannot be found?? */
suite: ;
    }

    /*
     * Si le sommet est etiquete 'inner', nous devons faire attention a etiqueter correctement
     * les pseudo-sommets liberes. Pour cela nous cherchons s'il existe un pseudo-sommet 'ib' fils de u
     * tel que ib->to_root soit hors de u. Nous numerotons alors alternativement les fils
     * afin que ia et ib soient tous les deux inner.
     ****
     * If the vertex is labeled 'inner', we must be careful to label correctly the freed pseudo-vertices.
     * For this we look if there exists a pseudo-vertex 'ib' son of u such that ib->to_root is out of u.
     * We then number alternatively the children such that ia and ib are both inner.
     */
    ib = NULL;
    if (u->label == LBL_INNER) {
        TRACE((stderr, "Recherche d'un point d'aboutissement\n"));
        c = u->to_root; assert(c);
        assert(c->label == LBL_OUTER);
        for (i = 0; i < NumberOfVertices; i++)
            if (vertices_list[i].externe == c)
                for (j = 0; j < NumberOfVertices; j++)
                    if (vertices_list[j].externe ==u && e_star[i][j]) {
                        /* Trouver le fils - Find the son */
                        for (t = vertices_list[j].interne;
                            t && t->parent != u; t = t->parent)  ;
                        assert(t->parent == u);
                        ib = t;
                        ib->to_root = c;
                        goto st2;
                    }
        assert(0);  /* point d'aboutissement introuvable?? - Cannot find endpoint?? */
st2:    assert(ib);
    }

    /* Nous devons maintenant apparier les enfants autres que 'ia'
     ****
     * We must now pair children other than 'ia'
     */
    TRACE((stderr, "Appariement des fils\n"));
    for (t = ia, i = -1; ; ) {
        t = t->next ? t->next : u->oldest;
        if (t == ia)
            break;
        t1 = t;
        t = t->next ? t->next : u->oldest;
        t1->partner = t;
        t->partner = t1;
        if (t1 == ib)
            i = 1;
        else if (t == ib)
            i = 0;
    }
    if (ia == ib)
        i = 0;

    /* Et labeller nos enfants - And label our children */
    TRACE((stderr, "Etiquetage des fils\n"));
    ia->label = u->label;
    if (i == 0) {
        assert(ib);
        t = ia;
        while (1) {
            t->label = LBL_INNER;
            if (t == ib) break;
            t1 = t;
            t = t->next ? t->next : u->oldest;
            t ->label = LBL_OUTER;
            t1->to_root = t;
            t1 = t;
            t = t->next ? t->next : u->oldest;
            t1->to_root = t;
        }
        assert(t == ib);
        t = t->next ? t->next : u->oldest;
        while (t != ia) {
            t->label = UNLABELLED;
            t->to_root = NULL;
            t = t->next ? t->next : u->oldest;
        }
    } else if (i == 1) {
        assert(ib);
        t = ib;
        while (1) {
            t->label = LBL_INNER;
            if (t == ia) break;
            t1 = t;
            t = t->next ? t->next : u->oldest;
            t->label = LBL_OUTER;
            t->to_root = t1;
            t1 = t;
            t = t->next ? t->next : u->oldest;
            t->to_root = t1;
        }
        assert(t == ia);
        t = t->next ? t->next : u->oldest;
        while (t != ib) {
            t->label = UNLABELLED;
            t->to_root = NULL;
            t = t->next ? t->next : u->oldest;
        }
    } else {
        assert(ib == NULL);
        /*
         * Si nous arrivons ici, c'est que le blossom n'est pas inner,
         * donc que nous sommes dans la phase finale d'expansion des blossoms.
         * Il ne faut surtout pas que les fils soient etiquetes 'inner',
         * sinon expand_blossom() rechercherait un point d'aboutissement
         ****
         * If we arrive here, that means the blossom isn't inner and that we are in the final phase
         * of blossoms expansion. Therefore sons must not be labeled 'inner' otherwise
         * expand_blossom() will look for an endpoint.
         */
        for (t = u->oldest; t; t = t->next) {
            t->label = UNLABELLED;
            t->to_root = NULL;
        }
    }

    assert(ib == NULL || ia->label == LBL_INNER);
    assert(ib == NULL || ib->label == LBL_INNER);

    /* Unlinker u de la liste des sommets externes - Unlink u from external vertices list */
    TRACE((stderr, "Mise a jour de la liste des pseudo-sommets\n"));
    old = &pseudo_vertices_list;
    for (t = *old; t && t != u; t = t->next)
        old = &(t->next);
    assert(*old == u);
    *old = u->oldest;

    /* Suivre la liste des fils jusqu'au bout - Follow sons' list to the end */
    old = &(u->oldest);
    for (t = *old; t; t = t->next) {
        t->parent = NULL;
        old = &(t->next);
    }
    assert(*old == NULL);
    *old = u->next;

    /* Nous n'avons plus besoin de u - u is no more needed */
    destroy_pseudo_vertex(u);

    recompute_external_pv();
    TRACE((stderr, "Blossom totalement detruit ]\n"));
}

/*
 * Contracte un blossom delimite par deux pseudo-sommets etiquetes 'outer' dans l'arbre
 * de recherche, et renvoie un pointeur sur le pseudo-sommet obtenu.
 ****
 * Contract a blossom defined by two pseudo-vertices labeled 'outer' in search tree
 * and returns a pointer on the resulting pseudo-vertex
 */

static pseudo_vertex *contract_blossom(pseudo_vertex *u, pseudo_vertex *v) {
    long i, alpha, card, dist_u, dist_v;
    pseudo_vertex *up, *vp, *s, *t, *blossom, **old;

    TRACE((stderr, "Contraction d'un blossom [\n"));

    assert(u); assert(u->cardinal % 2); assert(u->parent == NULL);
    assert(u->label == LBL_OUTER);
    assert(v); assert(v->cardinal % 2); assert(v->parent == NULL);
    assert(v->label == LBL_OUTER);
    /*
     * La premiere etape consiste a trouver le point de rencontre des chemins
     * u->racine et v->racine ; on aura une situation du genre suivant :
     ****
     * First step is to find the meeting point of the two paths u->root and v->root;
     * We should have a situation as:
     *
     *                         *- ... -- U
     *                        /
     * Root/Racine -- ... -- S
     *                        \
     *                         *- ... -- V' -- ... -- V
     *
     * Le blossom sera alors U -- ... -- S -- ... -- V' -- ... -- V.
     ****
     * Blossom will then be  U -- ... -- S -- ... -- V' -- ... -- V.
     */
    for (dist_u = 0, t = u; t; t = t->to_root)
        ++dist_u;
    for (dist_v = 0, t = v; t; t = t->to_root)
        ++dist_v;
    if (dist_v > dist_u) {
        up = u;
        for (vp = v, i = 0; i < dist_v - dist_u; ++i) {
            assert(vp);
            vp = vp->to_root;
        }
    } else {
        vp = v;
        for (up = u, i = 0; i < dist_u - dist_v; ++i) {
            assert(up);
            up = up->to_root;
        }
    }
    /* Calcul de s et / Compute s and  alpha=dist(s,u')=dist(s,v') */
    TRACE((stderr, "Recherche du point de jonction\n"));
    s = up;
    t = vp;
    for (alpha = 0; s != t; ++alpha) {
        assert(s != NULL && t != NULL);
        s = s->to_root;
        t = t->to_root;
    }
    assert(s->label == LBL_OUTER);
    assert(s->to_root == s->partner);
    /*
     * Nous pouvons maintenant creer le pseudo-sommet et faire pointer ses fils sur lui
     ****
     * We can now create the pseudo-vertex and have its sons point on it.
     */
    TRACE((stderr, "Creation du parent\n"));
    card = 2*alpha + 1 + labs(dist_u - dist_v);
    assert(card%2 == 1);
    blossom = create_pseudo_vertex(card, LBL_OUTER);
    TRACE((stderr, "cardinal == %ld\n", card));
    for (t = u; t != s; t = t->to_root)
        t->parent = blossom;
    for (t = v; t != s; t = t->to_root)
        t->parent = blossom;
    s->parent = blossom;
    /*
     * Puis les problemes de partner ; seul S peut en avoir un
     ****
     * Then partner problems; only S can have one
     */
    TRACE((stderr, "Contraction du partner\n"));
    if ((t = s->partner) != NULL) {
        assert(t->partner == s);
        assert(t->parent == NULL);
        t->partner = blossom;
        blossom->partner = t;
    }
    /* Retirer les fils de la liste des sommets externes
     ****
     * Remove sons from external vertices list
     */
    TRACE((stderr, "Mise a jour des liens\n"));
    old = &pseudo_vertices_list;
    for (t = *old; t; t = t->next)
        if (t->parent == NULL) {
            *old = t;
            old = &(t->next);
        }
    *old = NULL;
    /* Puis ajouter "blossom" au debut de la liste - Then add "blossom" to start of list */
    blossom->next = pseudo_vertices_list;
    pseudo_vertices_list = blossom;

    /*
     * Maintenant que les fils ont ete unlinkes de la liste des pseudo-sommets externes,
     * nous pouvons utiliser le champ "next" pour les chainer,
     * et initialiser le champ "oldest" du parent
     ****
     * Now that sons have been unlinked from the external pseudo-vertices list, we can use
     * the "next" field to chain them, and initialize field "oldest" from parent.
     */
    TRACE((stderr, "Chainage des fils\n"));
    blossom->oldest = u;
    for (t = u; t != s; t = t->to_root)
        t->next = t->to_root;
    for (t = v; t != s; t = t->to_root)
        t->to_root->next = t;
    v->next = NULL;

    /*
     * Si un pseudo-sommet pointait (via ->to_root) vers un fils, il faut
     * le faire pointer sur blossom. Egalement, initialiser le champ blossom->to_root.
     ****
     * If a pseudo-vertex pointed (through ->to_root) towards a son, we must have it
     * pointing on blossom. Also, initialize blossom->to_root field.
     */
    TRACE((stderr, "Contraction de l'arbre de recherche\n"));
    for (t = pseudo_vertices_list; t; t = t->next)
        if (t->to_root && t->to_root->parent) {
            assert(t->to_root->parent == blossom);
            t->to_root = blossom;
        }
    blossom->to_root = s->to_root;
    assert(blossom->to_root == blossom->partner);

    recompute_external_pv();
    TRACE((stderr, "Blossom entierement cree ]\n"));
    return blossom;
}

/*
 * Enleve toutes les etiquettes et efface l'arbre de recherche
 ****
 * Remove all labels and clear search tree
 */

static void remove_labels(void) {
    pseudo_vertex *t;

    TRACE((stderr, "Effacement de toutes les etiquettes\n"));

    for (t = pseudo_vertices_list; t; t = t->next) {
        t->label = UNLABELLED;
        t->to_root = NULL;
    }
}

/*
 * Choix d'une racine. Renvoie 0 si ce n'est pas possible, cad si tous les sommets verifient
 * la "vertex slackness condition". Dans ce cas l'algorithme se termine. Renvoie 1 sinon.
 *
 * IMPORTANT: Cet algorithme n'est pas parfait, il peut occasionnellement renvoyer un vertex
 * verifiant deja la v.s.c., mais jamais deux fois. Au moins, cela evitera les boucles infinies.
 * (Un pseudo-vertex non apparie contient exactement un vertex non apparie, mais les structures
 * de donnees presentes ne stockent pas cette information; il faudra vraiment corriger ca un jour.)
 ****
 * Chosing a root. Returns 0 if it's not possible, ie: if all vertices verify the
 * "vertex slackness condition". In that case, the algorithm ends. Returns 1 otherwise.
 *
 * IMPORTANT: this algorithm isn't perfect, it can occasionally return a vertex already verifying
 * the v.s.c., but never twice. At least that will prevent infinite loops. (An unpaired pseudo-vertex
 * contains exactly one unpaired vertex, but data structures doesn't hold that information. It must
 * really be corrected one day.)
 */

static vertex *root;

static long Choose_root(void) {
    long i;
    vertex *v;
    static long last = -1;

    for (i = last+1; i < NumberOfVertices; i++) {
        v = vertices_list + i;
        if (v->externe->partner == NULL && (v->y) > (weight)0) {
            root = v;
            root->externe->label = LBL_OUTER;
            TRACE((stderr,"Nouvelle racine choisie: %ld\n",i));
            last = i;
            return 1;
        }
    }
    TRACE((stderr, "Tous les sommets sont satures\n"));
    last = -1;
    return 0;
}

/*
 * Expansion de tous les blossoms externes etiquetes 'inner' dont la
 * variable z est egale a zero
 ****
 * Expansion of all external blossoms labeled 'inner' whose variable z is null.
 */

static void expand_internal_pv(void) {
    pseudo_vertex *pv;

    TRACE((stderr, "Disparition d'un inner-blossom (au moins)\n"));
    do {
        for (pv = pseudo_vertices_list; pv; pv = pv->next)
            if (pv->label == LBL_INNER &&
            pv->cardinal > 1 && pv->z == (weight)0) {
                expand_blossom(pv);
                break;
            }
    } while (pv);
}

/*
 * Expansion de tous les blossoms restants - Expand all remaining blossoms
 */

static void expand_all_blossoms(void) {
    pseudo_vertex *pv;

    TRACE((stderr, "Disparition de tous les blossoms restants\n"));
    do {
        for (pv = pseudo_vertices_list; pv; pv = pv->next)
            if (pv->parent == NULL && pv->cardinal > 1) {
                expand_blossom(pv);
                break;
            }
    } while (pv);
}

/*
 * Ajoute des aretes au graphe G', et renvoie le nombre d'aretes ajoutees. Attention, certaines aretes
 * de G' peuvent aussi disparaitre (mais pas celles de l'arbre de recherche,
 * ni celles qui sont enfermees dans un blossom).
 *
 * Cette fonction n'est pas utilisable pour initialiser E*, mais uniquement pour le recalculer
 * apres DVC. La valeur de delta doit etre STRICTEMENT POSITIVE.
 ****
 * Adds edges to graph G', and returns number of added edges. Attention, some edges of G' may also
 * disappear (not the ones in the search tree, nor those locked in a blossom).
 *
 * This function cannot be used to initialize E* but only to recalculate it after DVC.
 * Delta value must be STRICTLY POSITIVE.
 */

static long recompute_E_star(void) {
    long i, j, newa;
    vertex *u, *v;
    pseudo_vertex *u1, *v1;
    weight dif;

    TRACE((stderr, "Mise a jour de E*\n"));

#ifdef EDEBUG
    for (u1 = pseudo_vertices_list; u1; u1 = u1->next)
        if (u1->partner)
            assert(sont_voisins(u1,u1->partner));
#endif
    newa = 0;
    for (i = 0; i < NumberOfVertices; i++) {
        u = vertices_list + i;
        for (j = 0; j < i; j++) {
            v = vertices_list + j;
            /*
             * Si l'un des sommets est inner et l'autre outer, ou s'ils sont non etiquetes,
             * alors la valeur de e*(i,j) ne change pas. Meme chose si les deux sommets
             * sont contenus dans un meme pseudo-sommet.
             ****
             * If one vertex is inner and the other outer, or if they are unlabeled,
             * then value of e*(i,j) doesn't change. It's the same if both vertices are
             * contained in the same pseudo-vertex.
             */
            u1 = u->externe;
            v1 = v->externe;
            if ((u1 == v1) ||
                (u1->label == LBL_INNER  && v1->label == LBL_OUTER) ||
                (u1->label == LBL_OUTER  && v1->label == LBL_INNER) ||
                (u1->label == UNLABELLED && v1->label == UNLABELLED))
                continue;
            /*
             * Si l'un des sommets est inner, alors l'autre est inner ou unlabelled;
             * et on peut etre certain que l'arete va sortir du graphe,
             * car la quantite "dif" sera augmentee de (delta) ou (2*delta).
             ****
             * If one vertex is inner, then the other is inner or unlabeled; then we can be sure
             * the edge will be remove from the graph because "dif" quantity will be raised
             * by (delta) or (2*delta).
             */
            if ((u1->label == LBL_INNER) || (v1->label == LBL_INNER)) {
                if (e_star[i][j])
                    e_star[i][j] = e_star[j][i] = 0;
                continue;
            }
            /*
             * Sinon, l'un des label est outer et l'autre est outer ou bien unlabelled,
             * et il est possible qu'une arete soit ajoutee au graphe; et on peut etre certain
             * qu'elle n'y etait pas auparavant, car "dif" a strictement diminue.
             ****
             * Otherwise one of the labels is outer and the other is outer or unlabeled and
             * maybe an edge will be added to the graph; and we can be sure it was not present before
             * because "dif" has been strictly lowered.
             */
            dif = (u->y) + (v->y) - p[i][j];
            assert(dif >= (weight)0);
            assert(e_star[i][j] == 0);

            if (dif == (weight)0) {
                ++newa;
                e_star[i][j] = e_star[j][i] = 1;
            }
        } /* for (j...) */
    } /* for (i...) */
    TRACE((stderr, "Ajout de %ld arete(s) a E*\n", newa));

#ifdef EDEBUG
    for (u1 = pseudo_vertices_list; u1; u1 = u1->next)
        if (u1->partner)
            assert(sont_voisins(u1,u1->partner));
#endif
    return newa;
}

/*
 * Initialisation du graphe G' - Initialization of graph G'
 */

static void initialize_E_star(void) {
    long i, j;
    weight W;

    W = -INFINI;
    for (i = 0; i < NumberOfVertices; i++)
        for (j = 0; j < i; j++)
            if (p[i][j] > W)
                W = p[i][j];

    for (i = 0; i < NumberOfVertices; i++)
        for (j = 0; j < i; j++)
            e_star[i][j] = e_star[j][i] = (p[i][j] == W ? 1 : 0);

    W /= 2;
    TRACE((stderr, "W = %f\n", W));

    for (i = 0; i < NumberOfVertices; i++) {
        vertices_list[i].y = W;
        e_star[i][i] = 0;   /* On ne sait jamais... - You never know... */
    }
}

/*
 * Les trois procedures suivantes (DEV, DVC, HUT) font reference a des
 * variables communes delta, delta_[1-4], donc nous les definissons ici
 ****
 * The next three procedures (DEV, DVC, HUT) have references to shared
 * variables delta, delta_[1-4]. They are defined here.
 */

static weight delta, delta_1, delta_2, delta_3, delta_4;

/*
 * Delta EValuation procedure
 */

static void DEV (void) {
    long i, j;
    weight mu;
    pseudo_vertex *z;

    TRACE((stderr, "DEV\n"));

    delta_1 = INFINI;
    for (z = pseudo_vertices_list; z; z = z->next)
        if (z->cardinal > 1 && z->label == LBL_INNER)
            if (z->z < delta_1)
                delta_1 = z->z;
    delta_1 /= 2;

    delta_2 = INFINI;
    for (i = 0; i < NumberOfVertices; i++)
        if (vertices_list[i].externe->label == LBL_OUTER)
            if (vertices_list[i].y < delta_2)
                delta_2 = vertices_list[i].y;

    delta_3 = INFINI;
    for (i = 0; i < NumberOfVertices; i++)
        if (vertices_list[i].externe->label == LBL_OUTER)
        for (j = 0; j < NumberOfVertices; j++)
            if (vertices_list[j].externe->label == UNLABELLED) {
                mu = vertices_list[i].y + vertices_list[j].y - p[i][j];
                if (mu < delta_3)
                    delta_3 = mu;
            }

    delta_4 = INFINI;
    for (i = 0; i < NumberOfVertices; i++)
        if (vertices_list[i].externe->label == LBL_OUTER)
        for (j = 0; j < NumberOfVertices; j++)
            if (vertices_list[j].externe->label == LBL_OUTER &&
            vertices_list[i].externe < vertices_list[j].externe) {
                mu = vertices_list[i].y + vertices_list[j].y
                - p[i][j];
                if (mu < delta_4)
                delta_4 = mu;
            }
    delta_4 /= 2;

    delta = delta_1;
    if (delta_2 < delta)
        delta = delta_2;
    if (delta_3 < delta)
        delta = delta_3;
    if (delta_4 < delta)
        delta = delta_4;

    TRACE((stderr, "delta_1 = %e\n", delta_1));
    TRACE((stderr, "delta_2 = %e\n", delta_2));
    TRACE((stderr, "delta_3 = %e\n", delta_3));
    TRACE((stderr, "delta_4 = %e\n", delta_4));
    TRACE((stderr, "delta   = %e\n", delta  ));
}

/*
 * Dual Variables Change
 */

static void DVC (void) {
    long i;
    pseudo_vertex *t, *u;

    TRACE((stderr, "DVC\n"));

    /* D'abord verifier l'integrite des labels - First check labels integrity */
    for (t = pseudo_vertices_list; t; t = t->next)
        if (t->partner)
        assert(t->partner != t && t->partner->partner == t);

    for (t = pseudo_vertices_list; t; t = t->next)
        if ((u = t->partner) != NULL) {
        assert((t->label == LBL_INNER  && u->label == LBL_OUTER)
            || (t->label == LBL_OUTER  && u->label == LBL_INNER)
            || (t->label == UNLABELLED && u->label == UNLABELLED));
        }
    /* Ok */

    assert(delta >= (weight)0);
    if (delta == (weight)0)
        return;  /* Ne rien faire - Do nothing */

    for (i = 0; i < NumberOfVertices; i++)
        if (vertices_list[i].externe->label == LBL_OUTER) {
            vertices_list[i].y -= delta;
            assert(vertices_list[i].y >= (weight)0);
        } else if (vertices_list[i].externe->label == LBL_INNER)
            vertices_list[i].y += delta;

    for (t = pseudo_vertices_list; t; t = t->next)
        if (t->cardinal > 1) {
            if (t->label == LBL_OUTER)
                t->z += 2*delta;
            else if (t->label == LBL_INNER) {
                t->z -= 2*delta;
                assert(t->z >= (weight)0);
            }
        }
}

/*
 * Les fonctions raise_neutral(u) et raise_strong(u) prennent en argument un pseudo-sommet u
 * (deja apparie dans le premier cas, libre dans le second) contenant un sommet en lequel y=0.
 * On realise alors l'augmentation en echangeant, entre ce sommet et la racine,
 * les aretes qui appartiennent au couplage, et celles qui n'y appartiennent pas.
 *
 * Si l'argument u est NULL, ces fonctions cherchent un sommet u verifiant
 * les conditions ci-dessus.
 ****
 * Functions raise_neutral(u) and raise_strong(u) have as parameter a pseudo-vertex u (already
 * paired in the first case, free in the second) containing a vertex for which y=0.
 * Raising is then done by exchanging, between this vertex and root, edges part of the matching
 * and those not in it.
 *
 * If parameter u is NULL, these functions look for a vertex u verifying the above conditions.
 */

static void raise_neutral(pseudo_vertex *u) {
    long i;
    vertex *v;
    pseudo_vertex *pv;

    if (u) {
        TRACE((stderr,"Augmentation neutre\n"));
        assert(u->label == LBL_OUTER);
        assert(u->partner == u->to_root);
        u->partner = NULL;
        u = u->to_root;
        while (u) {
            pv = u->to_root;
            assert(pv);
#ifdef EDEBUG
            assert(sont_voisins(u,pv));
#endif
            u->partner = pv;
            pv->partner = u;
            u = pv->to_root;
        }
        return;
    }
    /* Nous devons nous-memes trouver u - We must find u ourselves */
    for (i = 0; i < NumberOfVertices; i++) {
        v = vertices_list + i;
        u = v->externe;
        if (u->label == LBL_OUTER && v->y == (weight)0) {
            raise_neutral(u);
            return;
        }
    }
    assert(0);
}

/*
 * Nous affichons un "+" a chaque augmentation forte; si le couplage de
 * poids maximum est un couplage parfait (ce sera toujours le cas pour nous)
 * alors il y aura exactement [NumberOfVertices/2] augmentations fortes.
 ****
 * A "+" is displayed for each strong raise; if the maximum weight matching is
 * a perfect one (and it will amways be the case for us), then there will exactly
 * be [NumberOfVertices/2] strong raises.
 */

static void raise_strong(pseudo_vertex *u) {
    pseudo_vertex *pv;

    if (u) {
        TRACE((stderr,"Augmentation forte\n"));
#ifndef EDEBUG
        if (!papp_tournament_api) fprintf(stderr, "+");
# ifdef __THINK_C__
        /*
         * Think-C bufferise stderr 'par ligne', il est donc
         * necessaire de vider explicitement le tampon ici
         ****
         * Think-C buffers stderr by lines, buffer must therefore
         * explicitely be flushed here.
         */
        fflush(stderr);
# endif
#endif
        assert(u->partner == NULL);
        pv = u->to_root;
        assert(pv != NULL);
        raise_neutral(pv);
        assert(pv->partner == NULL);
#ifdef EDEBUG
        assert(sont_voisins(u,pv));
#endif
        u->partner = pv;
        pv->partner = u;
        return;
    }
    /* Sinon, nous devons nous-memes trouver u
     ****
     * Otherwise we must find u ourselves */
    u = pseudo_vertices_list;
    while (u) {
        if (u->partner == NULL && u->to_root != NULL) {
            raise_strong(u);
            return;
        }
        u = u->next;
    }
    assert(0);
}


/*
 * La fonction HUT renvoie HUT_NEWROOT ou bien HUT_MAPS selon que
 * delta == delta_2 ou non
 ****
 * Function HUT returns HUT_NEWROOT or HUT_MAPS depending on whether
 * delta == delta_2 or not.
 */

#define HUT_NEWROOT     0
#define HUT_MAPS        1

static long HUT (void) {
    long newa;       /* Nb de nouvelles aretes de (E*) - Number of new edges in (E*) */

    TRACE((stderr, "HUT\n"));
    DEV();
    DVC();
    newa = (delta == (weight)0) ? 0 : recompute_E_star();

    if (delta == delta_1)
        expand_internal_pv();
    if (delta == delta_3 || delta == delta_4)
        assert(newa > 0);
    if (delta == delta_2 && root->y)
        raise_neutral(NULL);
    if (delta == delta_2) {
        /* La racine verifie la vertex slackness condition
         ****
         * Root checks vertex slackness condition  */
        remove_labels();
        return HUT_NEWROOT;
    }
    return HUT_MAPS;
}

/*
 * La procedure d'exploration, MAPS. Renvoie MAPS_AUGMENT si un chemin augmentant
 * a ete trouve, et MAPS_HUT si on a decouvert un arbre hongrois.
 *
 * NB: la contraction des blossoms a lieu ici, et non pas dans la boucle
 * principale. Nous n'avons donc pas a indiquer un troisieme code de sortie
 * pour signaler qu'un blossom a ete trouve.
 ****
 * Exploration function MAPS. Returns MAPS_AUGMENT if an augmenting path is found,
 * and MAPS_HUT if we have found an Hungarian tree.
 *
 * Note: blossoms contraction is done here and not in the main loop. We therefore do not
 * have to indicate a third return code to signal a blossom has been found.
 */

#define MAPS_AUGMENT    0
#define MAPS_HUT        1

static long MAPS (void) {
    pseudo_vertex *x, **xv, *y, *z;
    long explored;

    TRACE((stderr, "MAPS\n"));
    recompute_neighbors();
redo:
    explored = 0;

    for (x = pseudo_vertices_list; x; x = x->next)
        if (x->label == LBL_OUTER)
            for (xv = x->neighbours; (y = *xv) != NULL; ++xv) {
                if (y->label == LBL_INNER)
                    continue;
                if (y->partner == NULL && y->label == UNLABELLED) {
                    y->to_root = x;
                    return MAPS_AUGMENT;
                }
                if (y->label == LBL_OUTER) {
                    contract_blossom(x, y);
                    TRACE((stderr, "MAPS again\n"));
                    recompute_neighbors();
                    goto redo;
                }
                assert(y->label == UNLABELLED && y->partner != NULL);
                z = y->partner;
                assert(z->label == UNLABELLED && z->partner == y);
#ifdef EDEBUG
                assert(sont_voisins(y,z));
#endif
                y->to_root = x;
                z->to_root = y;
                y->label = LBL_INNER;
                z->label = LBL_OUTER;
                ++explored;
            }
    if (explored) {
        TRACE((stderr, "MAPS again\n"));
        goto redo;
    }
    return MAPS_HUT;
}

/*
 * Une fois tous les blossoms expandes, nous copions M dans le tableau t.
 ****
 * When all blossoms have been expanded, M is copied in array t.
 */

static void fill_t(void) {
    long i;
    pseudo_vertex *pv;

    for (i = 0; i < NumberOfVertices; i++)
        vertices_list[i].externe->label = i;
    for (i = 0; i < NumberOfVertices; i++) {
        pv = vertices_list[i].externe;
        if (pv->partner)
            T[i] = pv->partner->label;
        else    T[i] = i;
    }
}

/*
 * Routine principale - Main procedure
 */

static void edmonds (void) {
    initialize_E_star();

    while (Choose_root()) {
        while (1) {
            if (MAPS() == MAPS_AUGMENT) {
                raise_strong(NULL);
                remove_labels();
                break;
            } else if (HUT() == HUT_NEWROOT)
                break;
        }
    }
    remove_labels();
    expand_all_blossoms();
    fill_t();
}
