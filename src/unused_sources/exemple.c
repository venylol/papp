/*
 * exemple.c: produit des matrices de penalites aleatoires
 */

static char rcsid[] = "$Id: exemple.c,v 1.2 1994/07/03 17:03:07 bousch Exp $";

#include <stdio.h>
#include <stdlib.h>

int main(int argc, char **argv) {
    int i, j, n, pen_min, pen_max;

    if (argc != 4) {
        fprintf(stderr,"Exemple <n> <minimum> <maximum>\n");
        exit(0); }
    n = atoi(argv[1]);
    pen_min = atoi(argv[2]);
    pen_max = atoi(argv[3]);
    if (pen_max < pen_min)  pen_max=pen_min+10;
    printf("%d\n\n", n);
    for (i=0; i<2*n; i++) {
        for (j=0; j<2*n; j++)
            printf("%4d ",
                i==j ? 0 : pen_min+rand()%(pen_max-pen_min+1));
        printf("\n");
    }
    return 0;
}

