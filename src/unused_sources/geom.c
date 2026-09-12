/*
 * geom.c: produit des matrices de penalites aleatoires, les penalites
 * suivant une distribution geometrique.
 */

static char rcsid[] = "$Id: geom.c,v 1.2 1994/07/03 17:03:07 bousch Exp $";

#include <stdio.h>
#include <stdlib.h>
#include <math.h>

int main(int argc, char **argv) {
    int i, j, n;

    if (argc != 2) {
        fprintf(stderr,"Geom <n>\n");
        exit(0); }
    n = atoi(argv[1]);
    printf("%d\n\n", n);
    for (i=0; i<2*n; i++) {
        for (j=0; j<2*n; j++)
            printf("%4d ",
                i==j ? 0 : (int)pow(10,1+0.003*(rand()%1000)));
        printf("\n");
    }
    return 0;
}

